"""Addressables 的 catalog.json：地址键 -> (资产路径, 包名)。

为什么必须有这一层
------------------
图标族的键可能散在多个包里（DLC 单位的立绘在 dlc_2 / dlc_3 里，主包那份是**旧图**），
而"把候选包都打开扫一遍"既慢又吃内存——实测 dlc_3 有 724 MB。
catalog 本身就是 Addressables 的权威索引，解析它就能**只开真正需要的那一个包**。

解析逻辑移植自数据侧的 analyze_external_full.py（那边已经跑通并核对过），
这里只做封装 + 惰性化。
"""

from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

from ba_mod_maker.core.assets.catalog_bin import catalog_index
from ba_mod_maker.core.errors import AssetError

CATALOG_FILENAME = "catalog.json"

#: 1.2.1 起游戏换成二进制的那份（读数与格式见 `catalog_bin.py`）。
CATALOG_BIN_FILENAME = "catalog.bin"


@dataclass(slots=True)
class AddressablesCatalog:
    """catalog 的查询视图。

    index 的键是**地址键原样**（含反斜杠、空格、&）。
    值是该键指向的 (internalId, bundle 文件名) 集合——
    一个键通常只有一条；outline 前缀 + 裸名 与 裸名 是**两条不同的键**，各指各的。
    """

    index: dict[str, set[tuple[str, str]]] = field(default_factory=dict)

    @classmethod
    def load(cls, aa_dir: Path) -> AddressablesCatalog | None:
        """读并解析 catalog；文件不在或格式不符返回 None（**降级用**）。

        ★ 2026-10-13（1.2.1）：游戏把 `catalog.json` 换成了二进制 `catalog.bin`。
        两条路都给**同一个类型**的东西，调用方不用知道是哪一种：

        - `catalog.json`（≤1.2.0）：JSON 明文，三个 base64 表自带"键 → 包"；
        - `catalog.bin`（≥1.2.1）：Addressables 的 BinaryStorageBuffer，资产键要靠**依赖链**
          才走到包（口径差别与实测读数写在 `catalog_bin.py`）。
        """
        path = aa_dir / CATALOG_FILENAME
        if path.is_file():
            try:
                raw = json.loads(path.read_bytes().decode("utf-8"))
                return cls(index=_parse(raw))
            except (OSError, ValueError, KeyError, ZeroDivisionError, struct.error):
                return None
        binary = aa_dir / CATALOG_BIN_FILENAME
        if not binary.is_file():
            return None
        try:
            return cls(index=catalog_index(binary))
        except (OSError, ValueError, KeyError, struct.error, AssetError):
            return None

    @property
    def key_count(self) -> int:
        return len(self.index)

    def lookups(self, key: str) -> tuple[tuple[str, str], ...]:
        """这个地址键指向哪些 (资产路径, 包名)。取不到返回空元组。"""
        return tuple(sorted(self.index.get(key, ())))

    def lookup(self, key: str) -> tuple[str, str] | None:
        """取唯一一条。有多条时取**路径里不带 outline 段**的那条。

        实测：同名纹理（实心 / 空心）在 bundle 里重名，容器靠路径区分；
        而 catalog 的键本身已经区分好了（裸键 vs outline 前缀键）。
        真出现同键多地址时优先实心那条——宁可给实心，也不要给线稿。
        """
        found = self.lookups(key)
        if not found:
            return None
        for asset_path, bundle in found:
            if "/outline/" not in asset_path.replace(chr(92), "/"):
                return asset_path, bundle
        return found[0]


def _parse(raw: dict) -> dict[str, set[tuple[str, str]]]:
    """把 catalog 的三个 base64 表解成「键 -> {(资产路径, 包名)}」。

    表结构（Addressables 1.x 的紧凑格式，实测）：

    - m_KeyDataString：keyCount + 紧密排列的 (类型字节 + 值)，边界由桶偏移给出；
    - m_BucketDataString：bucketCount + 每条 (keyData 偏移, entryCount, entryIdx...)；
    - m_EntryDataString：entryCount + 定长 entry，字段 0 = internalId 下标、字段 2 = 依赖的组 key。
    """
    key_data = base64.b64decode(raw["m_KeyDataString"])
    bucket_data = base64.b64decode(raw["m_BucketDataString"])
    entry_data = base64.b64decode(raw["m_EntryDataString"])
    internal_ids = raw["m_InternalIds"]

    # --- 桶表 ---
    bucket_count = struct.unpack_from("<i", bucket_data, 0)[0]
    offset = 4
    buckets: list[list[int]] = []
    key_offsets: list[int] = []
    for _ in range(bucket_count):
        key_offset, entry_count = struct.unpack_from("<ii", bucket_data, offset)
        offset += 8
        key_offsets.append(key_offset)
        buckets.append(list(struct.unpack_from(f"<{entry_count}i", bucket_data, offset)))
        offset += 4 * entry_count

    # --- 键表 ---
    ends = key_offsets[1:] + [len(key_data)]
    keys: list[object] = []
    for i, start in enumerate(key_offsets):
        kind = key_data[start]
        cursor = start + 1
        if kind == 0:
            size = struct.unpack_from("<i", key_data, cursor)[0]
            cursor += 4
            value = key_data[cursor : cursor + size].decode("utf-8", "replace")
            cursor += size
        elif kind == 1:
            size = struct.unpack_from("<i", key_data, cursor)[0]
            cursor += 4
            value = key_data[cursor : cursor + size].decode("utf-16-le", "replace")
            cursor += size
        elif kind == 2:
            value = struct.unpack_from("<H", key_data, cursor)[0]
            cursor += 2
        elif kind == 3:
            value = struct.unpack_from("<I", key_data, cursor)[0]
            cursor += 4
        elif kind == 4:
            value = struct.unpack_from("<i", key_data, cursor)[0]
            cursor += 4
        elif kind == 5:
            value = "HASH128:" + key_data[cursor : cursor + 16].hex()
            cursor += 16
        else:
            raise ValueError(f"未知的 key 类型 {kind} @{start}")
        if cursor != ends[i]:
            raise ValueError(f"key[{i}] 长度与桶边界不符")
        keys.append(value)

    # --- 条目表 ---
    entry_count = struct.unpack_from("<i", entry_data, 0)[0]
    if entry_count <= 0:
        # 空表是**合法输入**（夹具/极简 catalog 都会走到这里）。
        # 原实现直接 // entry_count ⇒ ZeroDivisionError（2026-10-13 写新用例时踩到）。
        return {}
    stride = (len(entry_data) - 4) // entry_count
    fields = stride // 4
    entries = [
        struct.unpack_from(f"<{fields}i", entry_data, 4 + i * stride) for i in range(entry_count)
    ]

    # --- 组 key -> 包集合（entry 的字段 2 指向一个"组"，组里装的是包条目）---
    bundle_of_entry: dict[int, str] = {}
    for index, entry in enumerate(entries):
        internal = internal_ids[entry[0]] if 0 <= entry[0] < len(internal_ids) else None
        if isinstance(internal, str) and internal.endswith(".bundle"):
            bundle_of_entry[index] = internal.replace(chr(92), "/").rsplit("/", 1)[-1]
    group_bundles: dict[int, set[str]] = {}
    for index in range(len(keys)):
        names = {bundle_of_entry[e] for e in buckets[index] if e in bundle_of_entry}
        if names:
            group_bundles[index] = names

    # --- 键 -> 地址 ---
    index: dict[str, set[tuple[str, str]]] = {}
    for key_index, key in enumerate(keys):
        if not isinstance(key, str):
            continue
        bucket = index.setdefault(key, set())
        for entry_index in buckets[key_index]:
            if not (0 <= entry_index < len(entries)):
                continue
            entry = entries[entry_index]
            internal = internal_ids[entry[0]] if 0 <= entry[0] < len(internal_ids) else None
            if not isinstance(internal, str):
                continue
            group = group_bundles.get(entry[2]) if 0 <= entry[2] < len(keys) else None
            if group:
                for bundle in group:
                    bucket.add((internal, bundle))
            else:
                bucket.add((internal, ""))
    return index


__all__ = ["CATALOG_FILENAME", "AddressablesCatalog"]
