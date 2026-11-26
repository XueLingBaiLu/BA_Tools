r"""Addressables 2.x 的**二进制 catalog**（BinaryStorageBuffer）读取器 —— 纯标准库。

## 为什么有这一层

1.2.0 及以前，游戏 `aa\` 下放的是 `catalog.json`（JSON 明文，我们能逐字节读写）；
**1.2.1 起换成 `catalog.bin`**（2026-10-13 当时实测 2,345,239 B；**2026-10-17 11:30 更新后是
2,755,558 B**），于是 `AddressablesCatalog.load()` 直接取不到键、
`asset_kernel/writer.py::catalog_entry()` 抛 `UnicodeDecodeError` —— 一整条地址链断掉。

## 这到底是什么格式（⛔ 不是 UnityFS、也不是 SerializedFile）

它是 Addressables 自己的 **BinaryStorageBuffer**：

```
[Header 8×u32][KeyData 数组 u32×2×N][字符串/对象区][条目 7×u32 ×M][依赖数组]
```

**权威出处（本机就有源码，不用联网猜）**：

* `com.unity.addressables@2.9.1`（= 游戏 `aa\settings.json` 的
  `m_AddressablesVersion`，**同一个版本**）
  `Runtime/ResourceManager/Util/BinaryStorageBuffer.cs` ——
  `Reader` / `ReadString` / `ReadObject` / `ObjectTypeData`
  与两个标志位 `kUnicodeStringFlag = 0x80000000`、`kClearFlagsMask = 0x3FFFFFFF`；
* 同包 `Runtime/ResourceLocators/ContentCatalogData.cs` ——
  `ResourceLocator.Header`（8×u32）· `KeyData{keyNameOffset, locationSetOffset}` ·
  `ResourceLocation.Serializer.Data{primaryKeyOffset, internalIdOffset, providerOffset,`
  `dependencySetOffset, dependencyHashValue, extraDataOffset, typeId}` ·
  `…SerializedData{hashId, bundleNameId, crc, bundleSize, commonId}`。

⇒ 本模块是那两份源码的**逐字段移植**，不是逆向猜出来的；每一条都能在源码里指到行。

## 与 JSON 那份的关系（判别位）

| | catalog.json（≤1.2.0） | catalog.bin（≥1.2.1） |
|---|---|---|
| 前 4 字节 | 0x7B（左花括号） | **magic 0x0DE38942** |
| 版本 | —— | **容器 version = 2**（`ContentCatalogData.kVersion`） |
| 键 | `m_KeyDataString` 桶表 | `KeyData[]`，键与桶**并成同一条目** |
| 能不能写 | ✅ 逐字节正控 | ✅ **也能**（见下），但只能**整份重编码** |

**本模块只读；写侧在 `catalog_bin_write.py`**（★ 2026-10-13 起）。

* 为什么必须整份重编码：KeyData 数组紧贴 id 串、**零空隙** ⇒ 加/删一条键要**重排后面 ~2.16 MB**；
* 写侧已达成**逐字节正控**：把真件读进来重编码一遍，产物 **2,345,239 B · 与真件同一个 sha256**；
* **外部 oracle**（比往返更强的尺子）：`aa/catalog.hash` 内容 == `hash128_hex(整份 catalog.bin)`；
* ⚠ **尺子盲区（刻意保留）**：把哈希种子换成 `(1,1)` 后往返**照样相同** ⇒ 「往返逐字节相同」
  **不能**证明哈希实现与 Unity 一致 —— 那一件事**只由 `aa/catalog.hash` 那条 oracle 建立**；
* ⛔ `catalog_edit.py` / `catalog_writer.py`（写侧那条 JSON 通道）遇到 `.bin` 必须报不支持。

## 判据一行（真件）

    .venv\Scripts\python.exe tools\catalog_baseline.py
      -> 2026-10-18 实测：27,764 键 · 20,614 条目 · 76 个 .bundle · 解析错误 0；
         76/76 个 m_BundleSize 等于磁盘字节

⚠ **下面的读数会随游戏更新换代**（2026-10-13 那一代是 22,492 / 17,963 / 2,345,239 B）。
真件读数**唯一的重记入口**是 `tools/catalog_baseline.py`；测试里那组常量（带重记命令与日期）
才是判据，别拿本文里的数字当基线。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from ba_mod_maker.core.errors import AssetError

#: 头部魔数（官方 = `nameof(ContentCatalogData).GetHashCode()`；实测 0x0DE38942）。
CATALOG_BIN_MAGIC = 0x0DE38942

#: 容器版本。官方源码 `const int kVersion = 2;`（1.x 系列是 1）。
CATALOG_BIN_VERSION = 2

_U32_MAX = 0xFFFFFFFF
_K_UNICODE = 0x80000000
_K_DYNAMIC = 0x40000000
_K_MASK = 0x3FFFFFFF

#: `Header` = 8×u32；`KeyData` = 2×u32；条目 = 7×u32；AssetBundle options = 5×u32。
_HEADER = struct.Struct("<8I")
_KEYDATA = struct.Struct("<2I")
_LOCATION = struct.Struct("<7I")
_AB_OPTIONS = struct.Struct("<5I")
_OBJECT_TYPE = struct.Struct("<2I")


@dataclass(slots=True)
class CatalogBinLocation:
    """一条条目（官方 `ResourceLocation.Serializer.Data`，7×u32）。"""

    offset: int
    primary_key: str | None
    internal_id: str | None
    provider: str | None
    dependency_offsets: tuple[int, ...] = ()
    dependency_hash: int = 0
    extra_offset: int = _U32_MAX
    type_class: str | None = None
    type_assembly: str | None = None

    @property
    def bundle_name(self) -> str | None:
        """内部 id 指向某个 `.bundle` 时给它的**文件名**（其余返回 None）。"""
        iid = (self.internal_id or "").replace("\\", "/")
        return iid.rsplit("/", 1)[-1] if iid.endswith(".bundle") else None


@dataclass(slots=True)
class CatalogBinBundle:
    """一个 `.bundle` 条目的登记读数（官方 `AssetBundleRequestOptions.SerializedData`）。"""

    bundle_name: str
    internal_id: str
    hash_id: str = ""
    internal_name: str = ""
    crc: int = 0
    bundle_size: int = 0
    location_offset: int = 0


@dataclass(slots=True)
class CatalogBin:
    """一份解出来的二进制 catalog（只读视图）。"""

    path: Path
    data: bytes
    magic: int = 0
    version: int = 0
    keys_offset: int = 0
    id_offset: int = 0
    locator_id: str = ""
    build_hash: str = ""
    key_data: tuple[tuple[int, int], ...] = ()
    keys: tuple[object, ...] = ()
    locations: dict[int, CatalogBinLocation] = field(default_factory=dict)
    key_bundle_names: dict[object, set[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    # ---------------- 基础读取 ----------------

    def u32(self, offset: int) -> int:
        return struct.unpack_from("<I", self.data, offset)[0]

    def _sized(self, offset: int) -> bytes:
        """内部别名（模块内自己用；外部别调）。"""
        return self.sized(offset)

    def sized(self, offset: int) -> bytes:
        """数组 / 字符串的通用编码：**4 字节长度前缀在数据前面**（官方同款）。"""
        if offset < 4 or offset > len(self.data):
            raise AssetError(f"catalog.bin 偏移越界：{offset}", hint="文件可能不是这个格式")
        (size,) = struct.unpack_from("<I", self.data, offset - 4)
        end = offset + size
        if end > len(self.data):
            raise AssetError(
                f"catalog.bin 声明长度 {size} @{offset} 越界（文件 {len(self.data)} B）"
            )
        return self.data[offset:end]

    def read_auto_string(self, object_id: int) -> str | None:
        """`ReadAutoEncodedString`：最高位 = UTF-16，否则 ASCII。"""
        if object_id == _U32_MAX:
            return None
        is_unicode = bool(object_id & _K_UNICODE)
        offset = (object_id & _K_MASK) if is_unicode else object_id
        raw = self._sized(offset)
        return raw.decode("utf-16-le" if is_unicode else "ascii", "replace")

    def read_string(self, object_id: int, separator: str = "") -> str | None:
        """`Reader.ReadString(id, sep)`。`sep=""` = 自动编码；否则按分隔符拼**分片链表**。"""
        if object_id == _U32_MAX:
            return None
        if not separator:
            return self.read_auto_string(object_id)
        if object_id & _K_DYNAMIC:
            parts: list[str] = []
            cursor = object_id & _K_MASK
            while cursor != _U32_MAX:
                string_id, next_id = struct.unpack_from("<II", self.data, cursor)
                parts.append(self.read_auto_string(string_id) or "")
                cursor = next_id
                if len(parts) > 8192:
                    raise AssetError("catalog.bin 的动态字符串链过长（疑似解错位）")
            # 写侧从**最后一个分片**开始建链、读侧从尾部倒着填 ⇒ 链序是反的。
            return separator.join(reversed(parts))
        return self.read_auto_string(object_id)

    def _read_object_type(self, object_id: int) -> tuple[str | None, str | None, int] | None:
        """`ReadObject(id)`：先 `ObjectTypeData{typeId, objectId}`，再由 typeId 解出类型名。"""
        if object_id == _U32_MAX:
            return None
        type_id, payload_id = _OBJECT_TYPE.unpack_from(self.data, object_id)
        assembly, class_name = self._read_type(type_id)
        return (assembly, class_name, payload_id)

    def _read_type(self, type_id: int) -> tuple[str | None, str | None]:
        """`TypeSerializer`：`{assemblyId, classId}`，两串都用 `.` 作分隔符。"""
        if type_id == _U32_MAX:
            return (None, None)
        assembly_id, class_id = _OBJECT_TYPE.unpack_from(self.data, type_id)
        return (self.read_string(assembly_id, "."), self.read_string(class_id, "."))

    def read_key(self, index: int) -> object:
        """键表第 index 条。键可以是 `System.String` / `System.Int32` / `System.Int64`。"""
        name_offset, _location_set = self.key_data[index]
        got = self._read_object_type(name_offset)
        if got is None:
            return None
        _assembly, class_name, payload = got
        if class_name == "System.String":
            string_id, separator = struct.unpack_from("<IH", self.data, payload)
            return self.read_string(string_id, chr(separator) if separator else "")
        if class_name == "System.Int32":
            return struct.unpack_from("<i", self.data, payload)[0]
        if class_name == "System.Int64":
            return struct.unpack_from("<q", self.data, payload)[0]
        raise AssetError(f"catalog.bin 里出现未知键类型：{class_name!r}")

    # ---------------- 条目 ----------------

    def _read_location(self, offset: int) -> CatalogBinLocation:
        primary, internal, provider, dep_set, dep_hash, extra, type_id = _LOCATION.unpack_from(
            self.data, offset
        )
        deps: tuple[int, ...] = ()
        if dep_set != _U32_MAX:
            raw = self._sized(dep_set)
            deps = tuple(struct.unpack_from(f"<{len(raw) // 4}I", raw))
        assembly, class_name = self._read_type(type_id)
        return CatalogBinLocation(
            offset=offset,
            primary_key=self.read_string(primary, "/"),
            internal_id=self.read_string(internal, "/"),
            provider=self.read_string(provider, "."),
            dependency_offsets=deps,
            dependency_hash=struct.unpack("<i", struct.pack("<I", dep_hash))[0],
            extra_offset=extra,
            type_class=class_name,
            type_assembly=assembly,
        )

    def location(self, offset: int) -> CatalogBinLocation | None:
        got = self.locations.get(offset)
        if got is not None:
            return got
        try:
            got = self._read_location(offset)
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"location {offset}: {exc!r}")
            return None
        self.locations[offset] = got
        return got

    def location_offsets(self) -> list[int]:
        """被键表引用到的全部条目偏移（去重排序）。"""
        out: set[int] = set()
        for _name, location_set in self.key_data:
            if location_set == _U32_MAX:
                continue
            raw = self._sized(location_set)
            for offset in struct.unpack_from(f"<{len(raw) // 4}I", raw):
                if offset != _U32_MAX:
                    out.add(offset)
        return sorted(out)

    def read_bundle_entry(self, location: CatalogBinLocation) -> CatalogBinBundle | None:
        """`.bundle` 条目的四件套读数：`m_Hash` / 内部名 / `m_Crc` / `m_BundleSize`。"""
        name = location.bundle_name
        if name is None or location.extra_offset == _U32_MAX:
            return None
        got = self._read_object_type(location.extra_offset)
        if got is None:
            return None
        _assembly, class_name, payload = got
        # 只认这一种：Provider 不是 AssetBundle 的条目没有这四个数。
        # ⚠ 实测（真件）：这里回来的类名是**全限定名**
        #   …ResourceProviders.AssetBundleRequestOptions，
        #   所以用 endswith 比较（官方源码里写的是短名）。
        if (class_name or "").rsplit(".", 1)[-1] != "AssetBundleRequestOptions":
            return None
        hash_id, name_id, crc, size, _common_id = _AB_OPTIONS.unpack_from(self.data, payload)
        # ⚠ 官方是 ReadString(sd.bundleNameId, 下划线) —— 分隔符是**下划线**（不是默认的自动编码）。
        return CatalogBinBundle(
            bundle_name=name,
            internal_id=location.internal_id or "",
            hash_id=self._hash128_text(hash_id),
            internal_name=self.read_string(name_id, "_") or "",
            crc=crc,
            bundle_size=size,
            location_offset=location.offset,
        )

    def _hash128_text(self, hash_id: int) -> str:
        """`Hash128` 存的是 16 字节（官方 `ReadValue<Hash128>`），读成 32 位十六进制。"""
        if hash_id == _U32_MAX or hash_id + 16 > len(self.data):
            return ""
        return self.data[hash_id : hash_id + 16].hex()


def looks_like_catalog_bin(raw: bytes) -> bool:
    """判别位：前 4 字节是不是二进制 catalog 的 magic（JSON 那份是 0x7B）。"""
    if len(raw) < _HEADER.size:
        return False
    magic, version = struct.unpack_from("<2I", raw, 0)
    return magic == CATALOG_BIN_MAGIC and 0 < version <= CATALOG_BIN_VERSION


def load_catalog_bin(path: Path | str, *, max_keys: int = 0) -> CatalogBin:
    """读一份 `catalog.bin`（**只读**）。

    判据（真件 1.2.1，**2026-10-18 重记**）：27,764 键 · 20,614 条目 · 76 个 `.bundle` ·
    解析错误 0 条（2026-10-13 那一代是 22,492 / 17,963；重记走 `tools/catalog_baseline.py`）。
    """
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AssetError(f"读不到 catalog：{path}", error=str(exc)) from None
    if not looks_like_catalog_bin(raw):
        raise AssetError(
            f"{path.name} 不是二进制 catalog（前 4 字节不是 magic {CATALOG_BIN_MAGIC:#x}）",
            hint="1.2.0 及以前是 catalog.json；两个都认的入口见 core/assets/game.py::catalog_file",
        )
    (magic, version, keys_offset, id_offset, _ip, _sp, _io, build_hash) = _HEADER.unpack_from(
        raw, 0
    )
    parsed = CatalogBin(
        path=path,
        data=raw,
        magic=magic,
        version=version,
        keys_offset=keys_offset,
        id_offset=id_offset,
    )
    parsed.locator_id = parsed.read_auto_string(id_offset) or ""
    parsed.build_hash = parsed.read_string(build_hash, "") or ""

    raw_keys = parsed.sized(keys_offset)
    count = len(raw_keys) // _KEYDATA.size
    parsed.key_data = tuple(_KEYDATA.unpack_from(raw_keys, i * _KEYDATA.size) for i in range(count))

    limit = max_keys if max_keys > 0 else count
    keys: list[object] = []
    for i in range(min(count, limit)):
        try:
            keys.append(parsed.read_key(i))
        except Exception as exc:  # noqa: BLE001
            parsed.errors.append(f"key[{i}]: {exc!r}")
            keys.append(None)
    parsed.keys = tuple(keys)

    key_bundles: dict[object, set[str]] = {}
    for index, key in enumerate(parsed.keys):
        if key is None:
            continue
        _name, location_set = parsed.key_data[index]
        if location_set == _U32_MAX:
            continue
        raw_set = parsed.sized(location_set)
        names: set[str] = set()
        for offset in struct.unpack_from(f"<{len(raw_set) // 4}I", raw_set):
            if offset == _U32_MAX:
                continue
            location = parsed.location(offset)
            name = location.bundle_name if location else None
            if name:
                names.add(name)
        if names:
            key_bundles[key] = names
    parsed.key_bundle_names = key_bundles
    return parsed


def catalog_index(path: Path | str) -> dict[str, set[tuple[str, str]]]:
    """`catalog.bin` -> **与 JSON 通道同构**的索引：键 -> {(资产路径, 包名)}。

    ## ★ 二进制这一版**要靠依赖链**（这是与 JSON 最大的口径差别）

    实测（真件 1.2.1，**2026-10-18 重记**）：27,764 个键里只有 **76 个**直连 `.bundle`
    （那 76 个就是包名键）；
    而 3,513 个 `Assets/…` 资产键的条目 `internal_id` **就是资产路径本身**，它的 `dependencies`
    才是那个资产**可能落在的那一串包**（实测 A_MissionEditor.prefab 的依赖 = 23 个包）。

    ⇒ 本函数对这类条目**顺着依赖走一层**，把包集合取回来；
    「到底落哪一个」**不在这里猜** —— 产品原来的口径本来就是"给候选集合、由调用方按
    `m_Container` 复核成唯一命中"（`resolver.py::_ordered` 与 `graph.locate` 都这么做）。
    """
    parsed = load_catalog_bin(path)
    index: dict[str, set[tuple[str, str]]] = {}
    for index_no, key in enumerate(parsed.keys):
        if key is None:
            continue
        _name, location_set = parsed.key_data[index_no]
        if location_set == _U32_MAX:
            continue
        raw_set = parsed.sized(location_set)
        rows: set[tuple[str, str]] = set()
        for offset in struct.unpack_from(f"<{len(raw_set) // 4}I", raw_set):
            if offset == _U32_MAX:
                continue
            location = parsed.location(offset)
            if location is None:
                continue
            if location.bundle_name is not None:
                # 包名键自己直连（实测只有 76 个这种键）
                rows.add((str(location.internal_id or ""), location.bundle_name))
                continue
            asset_path = str(location.internal_id or "")
            for dep in location.dependency_offsets:
                dep_loc = parsed.location(dep)
                name = dep_loc.bundle_name if dep_loc else None
                if name:
                    rows.add((asset_path, name))
        if rows:
            index[str(key)] = rows
    return index


def bundle_entries(path: Path | str) -> dict[str, CatalogBinBundle]:
    """`.bundle` 名 -> 四件套读数（`m_Hash` / `m_Crc` / `m_BundleSize` / 内部名）。

    用途：装机前「catalog 四件套」那条检查（原来只走 JSON 通道）。
    """
    parsed = load_catalog_bin(path)
    out: dict[str, CatalogBinBundle] = {}
    for offset in parsed.location_offsets():
        location = parsed.location(offset)
        if location is None:
            continue
        entry = parsed.read_bundle_entry(location)
        if entry is not None:
            out[entry.bundle_name] = entry
    return out
