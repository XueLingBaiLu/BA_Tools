r"""catalog.json 的**全结构**读写：新增 / 删除（地址层 —— §0.10 只有这两种原子操作）。

## 这一模块解决什么

asset_kernel/catalog_writer.py 只会**追加**（六件套 + 三个计数头），删不掉东西：
删一条登记要动的是**所有引用它的下标与偏移**，字符串替换 / 抄旧偏移都会**静默**错。
本模块把 catalog 解析成一份**可重编号的结构**，于是两种操作各自可撤销：

    新增一个包登记（add_bundle）   -> 删除它（remove_bundle）   => 产物与原文件**逐字节相等**
    新增一条资产地址（add_address）-> 删除它（remove_address） => 同上

## 全结构（**那是 2026-10-06 的快照**：本机 catalog.json 4,016,273 B / sha256 29D4A40C…）

> ⚠ **表里的数是快照，游戏每次更新都会变** —— 复算：**2026-10-11 基线 catalog = 4,017,783 B**，
> **28,940 键 / 28,940 桶 / 21,276 entry / 14,456 个 internalId**（`address add` 一条之后各 +1；
> `skin install` 改包之后 catalog 的 sha 会变，那是**成对更新** m_Crc/m_BundleSize，不是结构变了）。
> ⇒ **结构**（三张表各自的计数头 + 紧密排列 + 桶 i 对应键 i）照下表；**数字别照抄，现算**。

| 表 | 读数（复算：_work/catalog_addr_probe.py） |
|---|---|
| m_KeyDataString | 计数头 28,937 + 键对象**紧密排列**；桶 i 的 keyDataOffset == 第 i 条键起点 |
| m_BucketDataString | 计数头 28,937 + 每桶 (keyDataOffset, 成员数, 成员下标…)；**桶 i 对应键 i** |
| m_EntryDataString | 计数头 21,274 + 每条 28 B = 7 乘 int32 |
| m_ExtraDataString | **没有计数头**：SerializedType 对象紧密排列；首尾正好铺满整表 |
| m_InternalIds | JSON 数组 14,454 条；其中 **6,686 条被多条 entry 共用** => 删一条地址时不许盲删 |

## 删除的五条硬规则（每条都有负控）

1. **条目是共享的 —— 删一条地址不许把条目一起删**。实测 live：21,197 个资产条目
   **21,197 个**都还有别的键指着它（地址键与 GUID 键成对，共 29,855 个「非本键」的 (条目,桶) 对）。
   规则：**还有别的桶引用它 => 留着**（并把它 entry[5] 重指到另一条还指着它的键）；
   **一条也没有 => 才跟着删**。
2. **包条目反过来：删包要连条目一起删** —— 它的「非本键桶」实测 **204/204 全是依赖组键**；
   组里少一个已经不存在的包是删除的应有之义。但若还有**地址键**指着它，一律抛。
3. **内部 id 只在「删完没人引用」时才删** —— 6,686 个 internalId 被多条 entry 共用，
   盲删会让别的条目指向错的那个字符串（不报错、只是指错）。
4. **删完所有下标/偏移重编号** —— entry[0]（InternalId）/ entry[2]（依赖键）/
   entry[5]（primaryKey）/ 桶里的成员下标 / 桶的 keyDataOffset / entry[4]（extra 偏移），
   一个都不能漏。
   不许字符串替换、不许复制旧偏移。
5. **会删出悬空引用的一律抛错** —— 删包时如果还有别的条目把它的键当依赖键
   （实测本机 **39/77** 个包名被这么用），必须先删那些地址；不许留悬空。
6. **包条目还列在依赖组的桶里** —— 实测 77 个包条目里 **48 个**同时列在别的桶里
   （有 1 个甚至列在 37 个桶里）。删除要把这个条目从**所有**桶里摘掉 ——
   「组里少一个已经不存在的包」是删除的应有之义；删了几处记在 RemovalReport 里。

## 与 catalog_writer 的关系

本模块**不改** catalog_writer.py，只用它：编码（key_bytes / extra_data_blob）、
常量（RuntimePath / Provider 下标 / 17 哨兵）与读侧自检（CatalogTable.from_raw 的结构校验、
check_pairs）。新增那半与它**同一套口径**，用例里做逐字节对拍
（tests/test_catalog_edit.py::test_p3_add_matches_catalog_writer_byte_for_byte）。

## 边界

* 只认 7 乘 int32 的 entry 步长（换游戏版本先复核，与 catalog_writer 同口径）；
* 不认游戏路径：调用方给什么路径就写什么路径；
* to_bytes() 会先跑一遍全结构自检，**不合法就抛**（宁可写不出去，也不要把 catalog 写坏）。
"""

from __future__ import annotations

import base64
import json
import struct
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import catalog_writer as cw
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "ADDRESS_ENTRY_SLOTS",
    "ENTRY_DEPENDENCY_HASH",
    "ENTRY_DEPENDENCY_KEY",
    "ENTRY_EXTRA_OFFSET",
    "ENTRY_INTERNAL_ID",
    "ENTRY_PRIMARY_KEY",
    "ENTRY_PROVIDER",
    "ENTRY_RESOURCE_TYPE",
    "BundleUpdate",
    "CatalogEdit",
    "RemovalReport",
    "extra_object_length",
    "load_catalog_edit",
]

#: entry 的 7 个 int32 各是哪一格（与 catalog_writer 的元组位置一一对应）。
ENTRY_INTERNAL_ID = 0
ENTRY_PROVIDER = 1
ENTRY_DEPENDENCY_KEY = 2
ENTRY_DEPENDENCY_HASH = 3
ENTRY_EXTRA_OFFSET = 4
ENTRY_PRIMARY_KEY = 5
ENTRY_RESOURCE_TYPE = 6

#: 一条 entry 的格数（实测 7；本版只认 7）。
ADDRESS_ENTRY_SLOTS = cw.ENTRY_FIELDS

#: catalog.json 的序列化口径（与 catalog_writer / 磁盘同一组参数）。
_JSON_KW: dict[str, Any] = {"separators": (",", ":"), "ensure_ascii": False}

#: 三张二进制表各自的字段名（计数头就藏在这三张表的前 4 字节里）。
_TABLE_FIELDS = ("m_KeyDataString", "m_BucketDataString", "m_EntryDataString", "m_ExtraDataString")


# --------------------------------------------------------------------------- #
# 单件：base64 / 计数头 / 桶表 / 键切片 / 条目表 / extra 长度
# --------------------------------------------------------------------------- #
def _table_bytes(raw: Mapping[str, Any], name: str) -> bytes:
    """取一张 base64 表并解码；缺字段或不是字符串就抛。"""
    value = raw.get(name)
    if not isinstance(value, str):
        raise AssetError(f"catalog 缺 {name}（或它不是 base64 字符串）")
    return base64.b64decode(value)


def _table_headers(key_blob: bytes, bucket_blob: bytes, entry_blob: bytes) -> tuple[int, int, int]:
    """三张表的计数头（各是表首 4 字节的一个 int32）。"""
    for name, blob in (
        ("m_KeyDataString", key_blob),
        ("m_BucketDataString", bucket_blob),
        ("m_EntryDataString", entry_blob),
    ):
        if len(blob) < 4:
            raise AssetError(f"{name} 至少要有 4 字节的计数头")
    (key_count,) = struct.unpack_from("<i", key_blob, 0)
    (bucket_count,) = struct.unpack_from("<i", bucket_blob, 0)
    (entry_count,) = struct.unpack_from("<i", entry_blob, 0)
    if key_count <= 0 or bucket_count <= 0 or entry_count <= 0:
        raise AssetError(
            f"计数头非正：keyCount {key_count} / bucketCount {bucket_count}"
            f" / entryCount {entry_count}",
            hint="这三格是三张表的头 4 字节，非正说明表已经坏了",
        )
    if key_count != bucket_count:
        raise AssetError(
            f"keyCount {key_count} != bucketCount {bucket_count}",
            hint="本格式一个键一个桶（桶 i 对应键 i）；不等说明表已经坏了",
        )
    return key_count, bucket_count, entry_count


def _parse_buckets(blob: bytes, bucket_count: int) -> tuple[list[list[int]], list[int]]:
    """解桶表：返回（每桶的成员下标, 每桶的 keyDataOffset）。"""
    buckets: list[list[int]] = []
    key_offsets: list[int] = []
    pos = 4
    for index in range(bucket_count):
        if pos + 8 > len(blob):
            raise AssetError(
                f"m_BucketDataString 比声明的桶数（{bucket_count}）还短（第 {index} 个桶）"
            )
        (key_offset, member_count) = struct.unpack_from("<ii", blob, pos)
        pos += 8
        if member_count < 0 or pos + 4 * member_count > len(blob):
            raise AssetError(f"第 {index} 个桶声明的成员数 {member_count} 越出表尾")
        buckets.append(list(struct.unpack_from(f"<{member_count}i", blob, pos)))
        key_offsets.append(key_offset)
        pos += 4 * member_count
    if pos != len(blob):
        raise AssetError(
            f"m_BucketDataString 尾部多出 {len(blob) - pos} 字节（桶表与声明的桶数不符）",
            hint="桶表是紧密排列的，多出来的字节说明边界算错了",
        )
    return buckets, key_offsets


def _slice_keys(key_blob: bytes, key_offsets: Sequence[int]) -> list[bytes]:
    """按桶给出的起点把键表切成**原始字节**（不重新编码 —— 那才是逐字节往返的前提）。"""
    if not key_offsets:
        raise AssetError("catalog 一个桶都没有")
    if key_offsets[0] != 4:
        raise AssetError(
            f"第一个桶的 keyDataOffset 是 {key_offsets[0]}，不是 4",
            hint="键表 = 4 字节计数头 + 紧密排列的键对象，第一条必须从 4 起",
        )
    keys: list[bytes] = []
    for index, start in enumerate(key_offsets):
        end = key_offsets[index + 1] if index + 1 < len(key_offsets) else len(key_blob)
        if not start < end <= len(key_blob):
            raise AssetError(
                f"第 {index} 条键的边界 [{start}, {end}) 不合法（表长 {len(key_blob)}）"
            )
        keys.append(bytes(key_blob[start:end]))
    return keys


def _parse_entries(blob: bytes, entry_count: int) -> list[list[int]]:
    """解条目表（每条 7 乘 int32）。"""
    stride = cw.ENTRY_FIELDS * 4
    if len(blob) - 4 != entry_count * stride:
        raise AssetError(
            f"m_EntryDataString 的字节长与 entryCount {entry_count} 不符"
            f"（步长 {(len(blob) - 4) / entry_count} B）",
            hint=f"本版只认 {cw.ENTRY_FIELDS} 乘 int32 = {stride} B 一条；换了游戏版本先复核再动",
        )
    return [list(struct.unpack_from("<7i", blob, 4 + i * stride)) for i in range(entry_count)]


def _length_prefixed(blob: bytes, pos: int) -> int:
    """跳过一段 [1B 长度][长度 字节]，返回下一格的位置（越界即抛，别让切片静默截断）。"""
    if pos >= len(blob):
        raise AssetError(f"extra data 在 {pos} 处就断了（表长 {len(blob)}）")
    end = pos + 1 + blob[pos]
    if end > len(blob):
        raise AssetError(
            f"extra data 在 {pos} 处声明了一段越界的名字（到 {end}，表长 {len(blob)}）"
        )
    return end


def extra_object_length(blob: bytes | bytearray, offset: int) -> int:
    """一条 extra data 的字节长（SerializedType 头 + UTF-16LE JSON）。

    与 catalog_writer.entry_options 解的是同一段：

        [1B 0x07][1B 程序集名长 + 名][1B 类名长 + 类名][i32 JSON 字节长][JSON]

    删除时必须知道**这条占多少字节**才能把后面整体前移（不许拿「下一条的偏移」倒推 ——
    表尾那条没有下一条）。
    """
    if offset < 0 or offset >= len(blob):
        raise AssetError(f"extraDataOffset {offset} 越出 m_ExtraDataString（{len(blob)} B）")
    if blob[offset] != cw.EXTRA_TYPE_MARKER:
        raise AssetError(
            f"extraDataOffset {offset} 的首字节是 {blob[offset]:#x}，"
            f"不是 {cw.EXTRA_TYPE_MARKER:#x}",
            hint="偏移指错地方了（extraDataOffset 指的是整条 extra data 的开头）",
        )
    pos = _length_prefixed(blob, offset + 1)
    pos = _length_prefixed(blob, pos)
    if pos + 4 > len(blob):
        raise AssetError(f"extraDataOffset {offset} 处的 JSON 长度越界")
    (byte_len,) = struct.unpack_from("<i", blob, pos)
    pos += 4
    if byte_len <= 0 or pos + byte_len > len(blob):
        raise AssetError(f"extraDataOffset {offset} 声明的 JSON 字节长 {byte_len} 越界")
    return pos + byte_len - offset


# --------------------------------------------------------------------------- #
# 重编号的小工具
# --------------------------------------------------------------------------- #
def _renumber(count: int, dropped: set[int]) -> list[int | None]:
    """旧下标 -> 新下标（被删的给 None）。删一个之后所有大于它的下标减一。"""
    out: list[int | None] = []
    cursor = 0
    for index in range(count):
        if index in dropped:
            out.append(None)
        else:
            out.append(cursor)
            cursor += 1
    return out


def _must(mapping: Sequence[int | None], old: int, what: str) -> int:
    """查重编号表；指向被删下标 = 悬空引用 => 抛（这是「不许留悬空」的机械保证）。"""
    if not 0 <= old < len(mapping):
        raise AssetError(f"{what} 指向不存在的下标 {old}（范围 0..{len(mapping) - 1}）")
    new = mapping[old]
    if new is None:
        raise AssetError(
            f"{what} 指向的 {old} 正是要被删掉的那一条 => 会留下悬空引用",
            hint="先把它依赖的那条删掉（remove_address），或者不要删这一条",
        )
    return new


def _splice_out(blob: bytes, drops: Sequence[tuple[int, int]]) -> bytes:
    """把若干 [起点, 起点+长度) 段从字节串里切掉（其余字节**一个不动**）。"""
    out = bytearray()
    cursor = 0
    for start, size in sorted(drops):
        if start < cursor:
            raise AssetError(f"要删的 extra 段 [{start}, {start + size}) 与前一段重叠")
        out += blob[cursor:start]
        cursor = start + size
    out += blob[cursor:]
    return bytes(out)


def _shifted_offset(offset: int, drops: Sequence[tuple[int, int]]) -> int:
    """旧字节偏移 -> 新字节偏移（前面被切掉多少就前移多少）。"""
    shift = 0
    for start, size in sorted(drops):
        if start == offset:
            raise AssetError(f"字节偏移 {offset} 本身就是被删掉的那一段")
        if start < offset:
            shift += size
    return offset - shift


# --------------------------------------------------------------------------- #
# 删除账
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class BundleUpdate:
    """一次「更新既有包登记」的账（改了什么 + 长度怎么变的 + 谁跟着重编号）。"""

    bundle_name: str
    entry_index: int
    extra_offset: int
    extra_bytes_before: int
    extra_bytes_after: int
    changed_fields: tuple[str, ...]
    renumbered_entries: tuple[int, ...]

    @property
    def delta(self) -> int:
        return self.extra_bytes_after - self.extra_bytes_before

    def summary(self) -> str:
        return (
            f"更新包登记 {self.bundle_name!r}：条目 #{self.entry_index} 的 extra"
            f" @{self.extra_offset} {self.extra_bytes_before} -> {self.extra_bytes_after} B"
            f"（{self.delta:+d}）；改了 {list(self.changed_fields)}；"
            f"跟着重编号的条目 {len(self.renumbered_entries)} 条"
        )


@dataclass(frozen=True, slots=True)
class RemovalReport:
    """一次删除的账（删了什么 + 三个计数头怎么变的）。"""

    kind: str  # "bundle" / "address"
    key: str
    key_index: int
    entry_indices: tuple[int, ...]
    kept_entry_indices: tuple[int, ...]
    repointed_entries: tuple[int, ...]
    internal_id: str | None
    extra_bytes: int
    group_memberships_dropped: int
    counts_before: tuple[int, int, int]
    counts_after: tuple[int, int, int]

    def summary(self) -> str:
        return (
            f"删除{self.kind} {self.key!r}：键 #{self.key_index}"
            f" + 删掉条目 {list(self.entry_indices)} + 留着条目 {list(self.kept_entry_indices)}"
            f"（重指 primaryKey 的 {list(self.repointed_entries)}）"
            f" + internalId {self.internal_id!r} + extra {self.extra_bytes} B"
            f" + 依赖组里的成员关系 {self.group_memberships_dropped} 处；"
            f"计数头 {self.counts_before} -> {self.counts_after}"
        )


# --------------------------------------------------------------------------- #
# 全结构
# --------------------------------------------------------------------------- #
@dataclass
class CatalogEdit:
    """一份 catalog 的可重编号视图（五件套 + 其余字段原样保留）。

    与 catalog_writer.CatalogTable 的区别：那一份是**只追加**（下标只增不减、偏移只往后长），
    这一份的键 / 桶 / 条目 / 内部 id 都是**普通列表**，所以能删了以后整体重编号。
    """

    raw: dict[str, Any]
    internal_ids: list[str]
    keys: list[bytes]
    buckets: list[list[int]]
    entries: list[list[int]]
    extra_data: bytes
    key_offsets: list[int] = field(default_factory=list)

    # ---- 读 ----
    @classmethod
    def from_bytes(cls, data: bytes) -> CatalogEdit:
        """从磁盘字节读（先做**正控**：重新序列化必须逐字节回到原样）。

        ⛔ **1.2.1 起游戏发的是 `aa/catalog.bin`（二进制）—— 本模块不吃它**，要报不支持。
        这不是顺手加的类型检查：`core/assets/catalog_bin.py` 文件头把它写成了不变式
        （「`catalog_edit.py` / `catalog_writer.py`（写侧那条 JSON 通道）遇到 `.bin` 必须报
        不支持」）。2026-10-18 之前这里是**裸 `UnicodeDecodeError: 0x89 at position 1`**
        —— `tests/test_catalog_edit.py` 那 15 条真件用例就这么红的，症状看着像
        「产品读写坏了」，其实是**喂错了格式**。二进制那一侧的等价能力（同样带逐字节往返
        正控与外部 `catalog.hash` oracle）在 `core/assets/catalog_bin_write.py`。
        """
        from ba_mod_maker.core.assets.catalog_bin import (  # noqa: PLC0415 -- 只在这条判别位上拉
            looks_like_catalog_bin,
        )

        if looks_like_catalog_bin(data):
            raise AssetError(
                "这是 1.2.1 的 catalog.bin（二进制），不是 catalog.json —— JSON 全结构通道不吃它",
                hint=(
                    "二进制写侧用 ba_mod_maker.core.assets.catalog_bin_write"
                    "（read_model / roundtrip / add_bundle / add_address / delete_address）"
                ),
            )
        try:
            raw = json.loads(data.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise AssetError(
                "这份字节既不是 catalog.bin（magic 对不上），也不是 UTF-8 的 catalog.json",
                hint="先确认是哪一代 catalog：game.catalog_file() / game.catalog_json_file()",
            ) from exc
        if not isinstance(raw, dict):
            raise AssetError("catalog.json 的顶层不是一个 JSON 对象")
        if json.dumps(raw, **_JSON_KW).encode("utf-8") != data:
            raise AssetError(
                "catalog.json 重新序列化不是逐字节原样",
                hint="序列化口径与磁盘上那份不一致；宁可不写，也不要把 catalog 重排坏",
            )
        return cls.from_raw(raw)

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> CatalogEdit:
        data = dict(raw)
        if not isinstance(data.get("m_InternalIds"), list):
            raise AssetError("catalog 缺 m_InternalIds（或它不是列表）")
        blobs = {name: _table_bytes(data, name) for name in _TABLE_FIELDS}
        key_count, bucket_count, entry_count = _table_headers(
            blobs["m_KeyDataString"], blobs["m_BucketDataString"], blobs["m_EntryDataString"]
        )
        buckets, key_offsets = _parse_buckets(blobs["m_BucketDataString"], bucket_count)
        keys = _slice_keys(blobs["m_KeyDataString"], key_offsets)
        entries = _parse_entries(blobs["m_EntryDataString"], entry_count)
        if len(keys) != key_count:
            raise AssetError(f"按桶切出 {len(keys)} 条键，计数头声明 {key_count} 条")
        edit = cls(
            raw=data,
            internal_ids=[str(value) for value in data["m_InternalIds"]],
            keys=keys,
            buckets=buckets,
            entries=entries,
            extra_data=blobs["m_ExtraDataString"],
            key_offsets=list(key_offsets),
        )
        edit.assert_consistent()
        return edit

    @property
    def counts(self) -> tuple[int, int, int]:
        return (len(self.keys), len(self.buckets), len(self.entries))

    def key_index_of(self, value: Any) -> int | None:
        """按**解出来的键值**找下标（bundle 文件名 / 资产地址键都认）。"""
        table = self.to_table()
        for index in range(table.key_count):
            if table.key_value(index) == value:
                return index
        return None

    def dependent_entries(self, key_index: int) -> tuple[int, ...]:
        """把 key_index 当**依赖键**（entry[2]）的条目 —— 删这个键会让它们悬空。"""
        return tuple(
            index
            for index, row in enumerate(self.entries)
            if row[ENTRY_DEPENDENCY_KEY] == key_index
        )

    def owned_entries(self, key_index: int) -> tuple[int, ...]:
        """primaryKey（entry[5]）就是 key_index 的条目 —— 删这个键必须把它们一起删。"""
        members = self.buckets[key_index]
        return tuple(
            entry for entry in members if self.entries[entry][ENTRY_PRIMARY_KEY] == key_index
        )

    def dependents_of(self, bundle_name: str) -> tuple[str, ...]:
        """列出「把这个包当依赖键」的那些**地址键**（= 用户接下来要逐条删掉的东西）。

        删包被拒时，这份清单就是「先删谁」的答案：每一条都能用 remove_address 单独删掉
        （每条都是独立的原子删除，符合 AGENTS §0.10）。返回**键值**（地址串），去重 + 排序；
        同一个地址键拥有多条条目时只出现一次。
        """
        key_index = self.key_index_of(bundle_name)
        if key_index is None:
            raise AssetError(
                f"catalog 里没有 {bundle_name!r} 这条键",
                hint="包名要写 m_KeyDataString 里那条键（= bundle 文件名）",
            )
        table = self.to_table()
        names: list[str] = []
        for entry in self.dependent_entries(key_index):
            value = table.key_value(self.entries[entry][ENTRY_PRIMARY_KEY])
            names.append(value if isinstance(value, str) else f"<非字符串键 {value!r}>")
        return tuple(sorted(set(names)))

    def is_bundle_entry(self, entry_index: int) -> bool:
        """包条目：provider = AssetBundleProvider + 无依赖键 + internalId 以 .bundle 结尾。"""
        row = self.entries[entry_index]
        if row[ENTRY_PROVIDER] != cw.ASSET_BUNDLE_PROVIDER or row[ENTRY_DEPENDENCY_KEY] != -1:
            return False
        internal = self.internal_ids[row[ENTRY_INTERNAL_ID]]
        return internal.replace(chr(92), "/").endswith(".bundle")

    def owners_of_entries(self) -> dict[int, list[int]]:
        """条目下标 -> 引用它的那些键下标（一个条目常被多条键指着：地址键 + GUID 键）。"""
        owners: dict[int, list[int]] = {}
        for key_index, members in enumerate(self.buckets):
            for entry in members:
                owners.setdefault(entry, []).append(key_index)
        return owners

    def dependency_group_keys(self) -> set[int]:
        """被别的条目当**依赖键**（entry[2]）用的键 —— 它们的桶里装的是包条目。"""
        return {
            row[ENTRY_DEPENDENCY_KEY] for row in self.entries if row[ENTRY_DEPENDENCY_KEY] != -1
        }

    def dependency_hash_of(self, key_index: int) -> int | None:
        """从**既有条目**里抄这个依赖键的 hash（算法未知，见 catalog_writer 模块头）。"""
        for row in self.entries:
            if (
                row[ENTRY_DEPENDENCY_KEY] == key_index
                and row[ENTRY_DEPENDENCY_HASH] != cw.INVALID_DEPENDENCY_HASH
            ):
                return int(row[ENTRY_DEPENDENCY_HASH])
        return None

    # ---- 序列化 ----
    def key_data_bytes(self) -> bytes:
        return struct.pack("<i", len(self.keys)) + b"".join(self.keys)

    def key_offset_list(self) -> list[int]:
        """每条键在键表里的起点（键表 = 4 字节计数头 + 紧密排列）。"""
        offsets: list[int] = []
        pos = 4
        for blob in self.keys:
            offsets.append(pos)
            pos += len(blob)
        return offsets

    def bucket_data_bytes(self) -> bytes:
        offsets = self.key_offset_list()
        out = bytearray(struct.pack("<i", len(self.buckets)))
        for index, members in enumerate(self.buckets):
            out += struct.pack("<ii", offsets[index], len(members))
            out += struct.pack(f"<{len(members)}i", *members)
        return bytes(out)

    def entry_data_bytes(self) -> bytes:
        out = bytearray(struct.pack("<i", len(self.entries)))
        for row in self.entries:
            if len(row) != cw.ENTRY_FIELDS:
                raise AssetError(f"entry 要 {cw.ENTRY_FIELDS} 个 int32，拿到 {len(row)}")
            out += struct.pack("<7i", *row)
        return bytes(out)

    def to_raw(self) -> dict[str, Any]:
        """三张表的计数头与内容全部**按当前结构重新生成**（其余 JSON 字段原样保留）。"""
        out = dict(self.raw)
        out["m_InternalIds"] = list(self.internal_ids)
        out["m_KeyDataString"] = base64.b64encode(self.key_data_bytes()).decode("ascii")
        out["m_BucketDataString"] = base64.b64encode(self.bucket_data_bytes()).decode("ascii")
        out["m_EntryDataString"] = base64.b64encode(self.entry_data_bytes()).decode("ascii")
        out["m_ExtraDataString"] = base64.b64encode(self.extra_data).decode("ascii")
        return out

    def to_table(self) -> cw.CatalogTable:
        """把当前结构交给**现有读侧**（catalog_writer 的结构校验与 check_pairs 都在它身上）。"""
        return cw.CatalogTable.from_raw(self.to_raw())

    def to_bytes(self) -> bytes:
        """出磁盘字节；**先跑全结构自检**，不合法就抛（fail closed）。"""
        self.assert_consistent()
        return json.dumps(self.to_raw(), **_JSON_KW).encode("utf-8")

    # ---- 自检 ----
    def self_check(self) -> tuple[str, ...]:
        """全结构自检：所有引用都在范围内 + 每条 entry 都有键认领。

        （真件成立：2026-10-11 读数 **21,276/21,276**；旧快照 21,274。）
        """
        problems: list[str] = []
        if len(self.keys) != len(self.buckets):
            problems.append(f"键 {len(self.keys)} 条与桶 {len(self.buckets)} 个不配对")
        owners: dict[int, list[int]] = {}
        for key_index, members in enumerate(self.buckets):
            for entry in members:
                if not 0 <= entry < len(self.entries):
                    problems.append(
                        f"桶 {key_index} 的成员 {entry} 越出条目表（{len(self.entries)} 条）"
                    )
                    continue
                owners.setdefault(entry, []).append(key_index)
        for entry, row in enumerate(self.entries):
            if not 0 <= row[ENTRY_INTERNAL_ID] < len(self.internal_ids):
                problems.append(f"entry {entry} 的 InternalId 下标 {row[ENTRY_INTERNAL_ID]} 越界")
            if not -1 <= row[ENTRY_DEPENDENCY_KEY] < len(self.keys):
                problems.append(f"entry {entry} 的依赖键下标 {row[ENTRY_DEPENDENCY_KEY]} 越界")
            if not 0 <= row[ENTRY_PRIMARY_KEY] < len(self.keys):
                problems.append(f"entry {entry} 的 primaryKey {row[ENTRY_PRIMARY_KEY]} 越界")
            else:
                pointing = owners.get(entry, [])
                if row[ENTRY_PRIMARY_KEY] not in pointing:
                    problems.append(
                        f"entry {entry} 的 primaryKey {row[ENTRY_PRIMARY_KEY]} 不在指向它的桶"
                        f" {pointing} 里"
                    )
            if row[ENTRY_EXTRA_OFFSET] < -1:
                problems.append(f"entry {entry} 的 extraDataOffset {row[ENTRY_EXTRA_OFFSET]} 非法")
            elif row[ENTRY_EXTRA_OFFSET] >= 0:
                try:
                    extra_object_length(self.extra_data, row[ENTRY_EXTRA_OFFSET])
                except AssetError as exc:
                    problems.append(
                        f"entry {entry} 的 extraDataOffset {row[ENTRY_EXTRA_OFFSET]}：{exc}"
                    )
            if entry not in owners:
                problems.append(f"entry {entry} 没有任何桶指向它（删完就成死角）")
        return tuple(problems)

    def assert_consistent(self) -> None:
        problems = self.self_check()
        if problems:
            head = "；".join(problems[:5])
            more = f"（还有 {len(problems) - 5} 条）" if len(problems) > 5 else ""
            raise AssetError(
                f"catalog 结构自检不过：{head}{more}",
                hint="这是「不许留下悬空引用」那道闸；删完必须重编号，改完必须复核",
            )

    def counts_line(self) -> str:
        return (
            f"计数头 keyCount {len(self.keys):,} / bucketCount {len(self.buckets):,}"
            f" / entryCount {len(self.entries):,}；m_InternalIds {len(self.internal_ids):,}；"
            f"extra {len(self.extra_data):,} B"
        )

    # ---- 写（追加；与 catalog_writer.apply_* 同一套口径，用例逐字节对拍）----
    def add_internal_id(self, value: str) -> int:
        self.internal_ids.append(str(value))
        return len(self.internal_ids) - 1

    def add_key(self, value: Any, kind: int = cw.KEY_UTF8) -> int:
        return self.add_key_raw(cw.key_bytes(kind, value))

    def add_key_raw(self, blob: bytes) -> int:
        self.keys.append(bytes(blob))
        return len(self.keys) - 1

    def add_bucket(self, key_index: int, entry_index: int) -> int:
        """追加一个**单成员**桶（本版只新增 size-1 桶，与 catalog_writer 口径一致）。"""
        self.buckets.append([int(entry_index)])
        if key_index != len(self.buckets) - 1:
            raise AssetError(
                f"桶 {len(self.buckets) - 1} 与键 {key_index} 不配对",
                hint="本格式桶 i 对应键 i：新增键的下标必须正好是新桶的下标",
            )
        return len(self.buckets) - 1

    def add_entry(self, values: Sequence[int]) -> int:
        if len(values) != cw.ENTRY_FIELDS:
            raise AssetError(f"entry 要 {cw.ENTRY_FIELDS} 个 int32，拿到 {len(values)}")
        self.entries.append([int(value) for value in values])
        return len(self.entries) - 1

    def add_extra(self, blob: bytes) -> int:
        offset = len(self.extra_data)
        self.extra_data += blob
        return offset

    def add_bundle(self, bundle: cw.CatalogBundle) -> cw.BundleRegistration:
        """六件套：一条新包登记（entry[2] 一律 -1，不许抄模板条目的依赖键）。"""
        internal_id = bundle.internal_id or (cw.RUNTIME_PATH + cw.PC_SEGMENT + bundle.bundle_name)
        internal_index = self.add_internal_id(internal_id)
        extra_offset = self.add_extra(cw.extra_data_blob(bundle.options()))
        key_index = self.add_key(bundle.bundle_name, cw.KEY_UTF8)
        entry_index = self.add_entry(
            [
                internal_index,
                cw.ASSET_BUNDLE_PROVIDER,
                -1,
                cw.INVALID_DEPENDENCY_HASH,
                extra_offset,
                key_index,
                0,
            ]
        )
        bucket_index = self.add_bucket(key_index, entry_index)
        return cw.BundleRegistration(
            bundle_name=bundle.bundle_name,
            internal_id=internal_id,
            internal_id_index=internal_index,
            key_index=key_index,
            bucket_index=bucket_index,
            entry_index=entry_index,
            extra_offset=extra_offset,
        )

    def add_dependency_group(self, members: Sequence[int], *, value: int | None = None) -> int:
        """建一个**真件形状的依赖组键**（KEY_INT32，桶里装这一组**包条目**），返回键下标。

        真件读数（2026-10-06，本机 catalog.json）：**114 个组键**，值都是**整数**
        （如 1834751174 / -1657840255），桶里一律是 provider == 0 的包条目；
        **77 个组只装 1 条包条目** ⇒「一个包一个组」也是真件形状；
        同一个组里的 1,370 条地址**共用同一个 entry[3]**（组哈希是组级的）。

        value 不给就按成员包名派生一个**确定**的 int（crc32 转有符号 32 位）——
        ⛔ 不要拿它当「算出来的真哈希」：它只是个唯一标识（加载时不校验，见 kb/14）。
        """
        if not members:
            raise AssetError("依赖组至少要有一个包条目")
        for entry_index in members:
            row = self.entries[int(entry_index)]
            if row[ENTRY_PROVIDER] != cw.ASSET_BUNDLE_PROVIDER or row[ENTRY_DEPENDENCY_KEY] != -1:
                raise AssetError(
                    f"entry#{entry_index} 不是包条目（provider={row[ENTRY_PROVIDER]}、"
                    f"dependencyKey={row[ENTRY_DEPENDENCY_KEY]}）",
                    hint="真件 114/114 个依赖组的桶里全是 provider=0 的包条目",
                )
        if value is None:
            names = "|".join(self.internal_ids[self.entries[int(e)][ENTRY_INTERNAL_ID]]
                             for e in members)
            value = zlib.crc32(names.encode("utf-8", "surrogateescape"))
            if value >= 2**31:
                value -= 2**32          # KEY_INT32 是有符号的（真件里也有负数）
        key_index = self.add_key(int(value), cw.KEY_INT32)
        for entry_index in members:
            self.add_bucket(key_index, int(entry_index))
        return key_index

    def addresses_of_path(self, value: str) -> tuple[int, ...]:
        """这条资产路径（`m_InternalIds` 里那个字符串）**已经有哪些地址条目**（provider=2）。

        用途：给一个**已存在**的资产加别名时，依赖声明应当照抄它既有那条 ——
        实测（2026-10-11 真机）`address add` 把 `entry[2]` 填成「包自己那条键」时，
        跨包依赖（vfx / builtinshaders / ammoprefabs …）没被声明 ⇒ 游戏加载不到那颗 prefab。
        """
        return tuple(
            index
            for index, row in enumerate(self.entries)
            if row[1] == cw.BUNDLED_ASSET_PROVIDER and self.internal_ids[row[0]] == str(value)
        )

    def add_address(
        self,
        address: cw.CatalogAddress,
        *,
        dependency_key: int,
        dependency_hash: int | None = None,
    ) -> cw.AddressRegistration:
        """第 7 件：一条**资产地址**（依赖键 = 它所在包那条键的下标）。

        依赖键的口径（**如实写，别当成已验**）：

        1. **真件的形状**是「一条**独立的组键** + 桶里装**一整组包条目**」——
           实测本机 76/76 个依赖键的桶只装包条目；本模块按产品现有口径填的是
           **包自己那条键**（桶里只有它自己）；
        2. **游戏认不认这个形状**：**2026-10-11 真机验了「全量重写后游戏加载得动」那一半** ——
           `address add`（`CatalogEdit.to_bytes()` 全量重写，+138 B）装进真 catalog 后，
           用户报「军械库照常打开、CV-9035 照常显示」。
           ⚠ **仍未验**：「新键本身能不能被取到」（要有别的东西引用它）；W10 N5 只证明了 entry[3]
           的哈希不被校验，**依赖键的桶形状**是否被校验也还没单独证过
           （见 研究-模型与动画/00-依赖hash侦察-结论.md §5、§6）。
        """
        dep_hash = dependency_hash
        if dep_hash is None:
            dep_hash = self.dependency_hash_of(dependency_key)
        if dep_hash is None:
            dep_hash = cw.INVALID_DEPENDENCY_HASH
        internal_index = self.add_internal_id(address.asset_path)
        key_index = self.add_key(address.primary_key, cw.KEY_UTF8)
        entry_index = self.add_entry(
            [
                internal_index,
                cw.BUNDLED_ASSET_PROVIDER,
                dependency_key,
                int(dep_hash),
                -1,
                key_index,
                int(address.resource_type),
            ]
        )
        bucket_index = self.add_bucket(key_index, entry_index)
        return cw.AddressRegistration(
            primary_key=address.primary_key,
            asset_path=address.asset_path,
            internal_id_index=internal_index,
            key_index=key_index,
            bucket_index=bucket_index,
            entry_index=entry_index,
        )

    # ---- 写（更新：既有包登记的 extra）----
    def update_bundle(
        self,
        bundle_name: str,
        *,
        crc: int,
        bundle_size: int,
        m_hash: str | None = None,
    ) -> BundleUpdate:
        """更新一条**既有**包登记的 extra（m_Crc / m_BundleSize / 可选 m_Hash）。

        这是「往既有包里加东西」的**收尾记账**：包文件改了 => crc32(逐块解压拼接) 与文件长都变
        => catalog 那条 AssetBundleRequestOptions 必须跟着走（否则游戏按旧 CRC 取包）。
        它改的是**同一个包的同一条登记**，不是「换」：没有别的包被指过去。

        三件事必须同时成立：

        1. **只改那几个字段**：先跑正控 —— 不改任何字段时重新编码必须**逐字节**回到原样，
           不成立就抛（宁可不写，也不要把这条 JSON 重排坏）；
        2. **长度变了要重编号**：JSON 是 UTF-16LE、长度按**字节**，位数一变
           （9 位 -> 10 位 = **+2 B**）后面每条 entry 的 extraDataOffset 都要跟着平移
           （catalog_writer 文件头第 2 个坑）；
        3. 改完 assert_consistent()：每条偏移都还得指着一条**完整的** extra 对象。
        """
        key_index = self.key_index_of(bundle_name)
        if key_index is None:
            raise AssetError(
                f"catalog 里没有 {bundle_name!r} 这条键",
                hint="包名要写 m_KeyDataString 里那条键（= bundle 文件名）",
            )
        owned = self.owned_entries(key_index)
        if len(owned) != 1:
            raise AssetError(
                f"键 {bundle_name!r} 的桶里有 {len(owned)} 条 primaryKey 指向它的条目（期望 1 条）",
                hint="包条目一对一；不等说明这份 catalog 已经被改坏了",
            )
        entry_index = owned[0]
        if not self.is_bundle_entry(entry_index):
            raise AssetError(
                f"{bundle_name!r} 指着的那条不是包条目",
                hint="资产地址那条没有 AssetBundleRequestOptions，改 CRC 请找包登记那条键",
            )
        offset = self.entries[entry_index][ENTRY_EXTRA_OFFSET]
        if offset < 0:
            raise AssetError(f"包 {bundle_name!r} 的条目没有 extra（extraDataOffset = {offset}）")
        size = extra_object_length(self.extra_data, offset)
        for other, row in enumerate(self.entries):
            if other == entry_index:
                continue
            if offset <= row[ENTRY_EXTRA_OFFSET] < offset + size:
                raise AssetError(
                    f"entry {other} 的 extraDataOffset {row[ENTRY_EXTRA_OFFSET]} 落在这条 extra"
                    f" [{offset}, {offset + size}) 里",
                    hint="这条 extra 被多条条目共用；改它会连带改别人的登记，本版不许",
                )
        options = cw.entry_options(self.extra_data, offset)
        old_blob = bytes(self.extra_data[offset : offset + size])
        if cw.extra_data_blob(options) != old_blob:
            raise AssetError(
                "这条 extra 重新编码不是逐字节原样",
                hint="字段顺序 / 分隔符跟磁盘上那份不一致 —— 宁可不写，也不要把它重排坏",
            )
        updated = dict(options)
        changed: list[str] = []
        for name, value in (("m_Crc", int(crc)), ("m_BundleSize", int(bundle_size))):
            if updated.get(name) != value:
                updated[name] = value
                changed.append(name)
        if m_hash is not None and updated.get("m_Hash") != str(m_hash):
            updated["m_Hash"] = str(m_hash)
            changed.append("m_Hash")
        new_blob = cw.extra_data_blob(updated)
        delta = len(new_blob) - size
        renumbered: list[int] = []
        if delta:
            for index, row in enumerate(self.entries):
                if row[ENTRY_EXTRA_OFFSET] > offset:
                    row[ENTRY_EXTRA_OFFSET] += delta
                    renumbered.append(index)
        self.extra_data = self.extra_data[:offset] + new_blob + self.extra_data[offset + size :]
        self.assert_consistent()
        return BundleUpdate(
            bundle_name=bundle_name,
            entry_index=entry_index,
            extra_offset=offset,
            extra_bytes_before=size,
            extra_bytes_after=len(new_blob),
            changed_fields=tuple(changed),
            renumbered_entries=tuple(renumbered),
        )

    # ---- 写（删除：重编号）----
    def remove_bundle(self, bundle_name: str) -> RemovalReport:
        """删掉一个包登记：key + bucket + entry + extra + internalId（引用同步重编号）。

        如果还有别的条目把这个键当**依赖键**（真件里 39/77 个包名被这么用），
        删了就会留悬空 => 直接抛，要求先删掉那些地址。
        """
        key_index = self.key_index_of(bundle_name)
        if key_index is None:
            raise AssetError(
                f"catalog 里没有 {bundle_name!r} 这条键",
                hint="包名要写 m_KeyDataString 里那条键（= bundle 文件名）",
            )
        owned = self.owned_entries(key_index)
        if len(owned) != 1:
            raise AssetError(
                f"键 {bundle_name!r} 的桶里有 {len(owned)} 条 primaryKey 指向它的条目（期望 1 条）",
                hint="包条目一对一；不等说明这份 catalog 已经被改坏了",
            )
        entry_index = owned[0]
        if not self.is_bundle_entry(entry_index):
            raise AssetError(
                f"键 {bundle_name!r} 指向的条目不是包条目"
                f"（provider / 依赖键 / internalId 有一个不对）",
                hint="要删**资产地址**用 remove_address；这一条不是包登记",
            )
        dependents = tuple(e for e in self.dependent_entries(key_index) if e != entry_index)
        if dependents:
            addresses = self.dependents_of(bundle_name)
            raise AssetError(
                f"还有 {len(dependents)} 条资产条目把 {bundle_name!r} 当依赖键"
                f"（涉及 {len(addresses)} 条地址键，前几条：{list(addresses[:3])}）",
                hint="用 dependents_of(包名) 拿全清单，再逐条 remove_address"
                "（每条都是独立的原子删除，别级联）；不许留悬空依赖",
            )
        # 包条目一旦删，指着它的桶都要摘掉它 —— 那些桶必须是**依赖组键**
        # （实测 live：77 个包条目的「非本键桶」204/204 都是依赖组键）。
        # 若还有别的**地址键**指着它，删了就是把那条地址悄悄弄空 => 抛。
        groups = self.dependency_group_keys()
        strangers = [
            k
            for k in self.owners_of_entries().get(entry_index, ())
            if k != key_index and k not in groups
        ]
        if strangers:
            raise AssetError(
                f"还有 {len(strangers)} 条键（不是依赖组键）指着这个包条目：{list(strangers[:5])}…",
                hint="先把那些地址删掉，再删这个包",
            )
        return self._remove(
            kind="bundle",
            key=bundle_name,
            key_index=key_index,
            delete_entries=(entry_index,),
            keep_entries=(),
        )

    def remove_address(self, key: str) -> RemovalReport:
        """删掉一条资产地址：key + bucket（**条目按引用计数决定删还是留**）。

        真件实测（2026-10-06）：21,197 个资产条目里 **21,197 个**都还有别的键指着它
        （地址键与 GUID 键成对，共 29,855 个「非本键」的 (条目,桶) 对）。所以：

        * 还有别的桶引用它 => **留着**，并把它 entry[5] 重指到另一条还指着它的键
          （不重指就会悬空，而「共享」在真件里是常态）；
        * 一条也没有 => 才跟着删（「新增 -> 删除 = 逐字节回退」靠的就是这一支）。

        还有一支是**别名键**（真件里那种 GUID 键，它一条条目也不拥有）：只把这条键拿掉，
        桶里指着别人的条目**一条都不动**（口径定于 2026-10-06，用例 test_p5b 守着）。
        """
        key_index = self.key_index_of(key)
        if key_index is None:
            raise AssetError(
                f"catalog 里没有 {key!r} 这条键",
                hint="地址键要写原样（含反斜杠、空格、&）；先 key_index_of 查一遍",
            )
        owned = self.owned_entries(key_index)
        if any(self.is_bundle_entry(entry) for entry in owned):
            raise AssetError(
                f"{key!r} 是**包登记**那条键，不是资产地址",
                hint="删包用 remove_bundle（它还会检查有没有别的条目依赖这个包）",
            )
        owners = self.owners_of_entries()
        delete: list[int] = []
        keep: list[int] = []
        for entry in owned:
            others = [k for k in owners.get(entry, ()) if k != key_index]
            (keep if others else delete).append(entry)
        return self._remove(
            kind="address",
            key=key,
            key_index=key_index,
            delete_entries=tuple(delete),
            keep_entries=tuple(keep),
        )

    def _remove(
        self,
        *,
        kind: str,
        key: str,
        key_index: int,
        delete_entries: Sequence[int],
        keep_entries: Sequence[int] = (),
    ) -> RemovalReport:
        """删除的共同动作：定哪些条目跟着删、哪些留着（并重指 primaryKey），再重建 + 自检。"""
        before = self.counts
        entries_to_drop = set(delete_entries)
        entries_to_keep = set(keep_entries)
        if entries_to_drop & entries_to_keep:
            raise AssetError("同一条目不能既删又留")
        owners = self.owners_of_entries()
        for entry in sorted(entries_to_drop | entries_to_keep):
            if not 0 <= entry < len(self.entries):
                raise AssetError(f"要处理的条目下标 {entry} 越界（共 {len(self.entries)} 条）")
        # 悬空闸：删掉这条键之后，别的条目的 [2] / [5] 还指着它 => 抛
        for entry, row in enumerate(self.entries):
            if entry in entries_to_drop:
                continue
            if row[ENTRY_DEPENDENCY_KEY] == key_index:
                raise AssetError(
                    f"entry {entry} 的依赖键还是 {key!r}（要删的这条）",
                    hint="先删掉这些依赖它的条目，别留悬空引用",
                )
            if row[ENTRY_PRIMARY_KEY] == key_index and entry not in entries_to_keep:
                raise AssetError(
                    f"entry {entry} 的 primaryKey 还是 {key!r}（要删的这条）",
                    hint="还指着它的条目要么跟着删（delete_entries），要么留着并重指（keep_entries）",
                )
        # 留着的条目：必须还有别的桶引用它（这就是「共享」），并挑一条键重指 primaryKey
        repoint: dict[int, int] = {}
        for entry in sorted(entries_to_keep):
            others = [k for k in owners.get(entry, ()) if k != key_index]
            if not others:
                raise AssetError(
                    f"entry {entry} 除了 {key!r} 没有别的桶引用它 —— 留着就成死角",
                    hint="这种条目该跟着删，不该写进 keep_entries",
                )
            if self.entries[entry][ENTRY_PRIMARY_KEY] == key_index:
                repoint[entry] = min(others)
        # 一、extra：本条独占才删；有别人指着这一段就抛（切了就把别人指坏）
        extra_drops: list[tuple[int, int]] = []
        for entry in sorted(entries_to_drop):
            offset = self.entries[entry][ENTRY_EXTRA_OFFSET]
            if offset < 0:
                continue
            size = extra_object_length(self.extra_data, offset)
            for other, row in enumerate(self.entries):
                if other in entries_to_drop:
                    continue
                if offset <= row[ENTRY_EXTRA_OFFSET] < offset + size:
                    raise AssetError(
                        f"entry {other} 的 extraDataOffset {row[ENTRY_EXTRA_OFFSET]} 落在要删的"
                        f"那条 extra [{offset}, {offset + size}) 里",
                        hint="这条 extra 被多条 entry 共用；先删共用它的那些条目",
                    )
            extra_drops.append((offset, size))
        # 二、internalId：删完之后没有任何 entry 引用它才删（真件 6,686 条被共用）
        dropped_internal: set[int] = set()
        for internal in {self.entries[entry][ENTRY_INTERNAL_ID] for entry in entries_to_drop}:
            still_used = any(
                row[ENTRY_INTERNAL_ID] == internal
                for other, row in enumerate(self.entries)
                if other not in entries_to_drop
            )
            if not still_used:
                dropped_internal.add(internal)
        # 三、三张重编号表（都基于**删之前**的下标算）
        entry_map = _renumber(len(self.entries), entries_to_drop)
        key_map = _renumber(len(self.keys), {key_index})
        internal_map = _renumber(len(self.internal_ids), dropped_internal)
        if len(entries_to_drop) == 1 and len(dropped_internal) == 1:
            only = next(iter(entries_to_drop))
            removed_internal: str | None = self.internal_ids[self.entries[only][ENTRY_INTERNAL_ID]]
        else:
            removed_internal = None
        removed_extra = sum(size for _start, size in extra_drops)

        new_entries: list[list[int]] = []
        for index, row in enumerate(self.entries):
            if entry_map[index] is None:
                continue
            dependency = row[ENTRY_DEPENDENCY_KEY]
            primary = repoint.get(index, row[ENTRY_PRIMARY_KEY])
            new_entries.append(
                [
                    _must(internal_map, row[ENTRY_INTERNAL_ID], f"entry {index} 的 InternalId"),
                    row[ENTRY_PROVIDER],
                    -1
                    if dependency == -1
                    else _must(key_map, dependency, f"entry {index} 的依赖键"),
                    row[ENTRY_DEPENDENCY_HASH],
                    -1
                    if row[ENTRY_EXTRA_OFFSET] < 0
                    else _shifted_offset(row[ENTRY_EXTRA_OFFSET], extra_drops),
                    _must(key_map, primary, f"entry {index} 的 primaryKey"),
                    row[ENTRY_RESOURCE_TYPE],
                ]
            )
        # 四、桶：被删的条目要从**所有**桶里摘掉 —— 它同时还是依赖组的成员
        #    （实测：删 mapsettings 那个包时，它的条目正列在别的依赖组的桶里）。
        #    组里少一个已经不存在的包，这是删除的应有之义；不是悬空引用。
        new_buckets: list[list[int]] = []
        memberships_dropped = 0
        for index, members in enumerate(self.buckets):
            if key_map[index] is None:
                continue
            kept_members: list[int] = []
            for member in members:
                if member in entries_to_drop:
                    memberships_dropped += 1
                    continue
                kept_members.append(_must(entry_map, member, f"桶 {index} 的成员"))
            new_buckets.append(kept_members)

        self.internal_ids = [
            value
            for index, value in enumerate(self.internal_ids)
            if internal_map[index] is not None
        ]
        self.keys = [value for index, value in enumerate(self.keys) if key_map[index] is not None]
        self.extra_data = _splice_out(self.extra_data, extra_drops)
        self.entries = new_entries
        self.buckets = new_buckets
        self.key_offsets = self.key_offset_list()
        self.assert_consistent()
        return RemovalReport(
            kind=kind,
            key=key,
            key_index=key_index,
            entry_indices=tuple(sorted(entries_to_drop)),
            kept_entry_indices=tuple(sorted(entries_to_keep)),
            repointed_entries=tuple(sorted(repoint)),
            internal_id=removed_internal,
            extra_bytes=removed_extra,
            group_memberships_dropped=memberships_dropped,
            counts_before=before,
            counts_after=self.counts,
        )


def load_catalog_edit(path: str | Path) -> CatalogEdit:
    """读一份磁盘上的 catalog.json（带逐字节正控）。"""
    return CatalogEdit.from_bytes(Path(path).read_bytes())
