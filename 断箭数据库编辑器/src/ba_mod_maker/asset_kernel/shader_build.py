r"""W2 · 搬运真件 Shader：**按解析名找** -> 复制依赖闭包 -> PPtr 重映射 -> 直接进
`unit_bundle.assemble_unit_bundle» 的 extra。

## 这条路解决什么

一个自造单位包要能画出来，材质的 `m_Shader» 必须指到**包内**一个真 Shader 对象
（`unit_bundle.assemble_unit_bundle» 的 N4 尺子：`fileID=0» 的 pathID 必须落在对象表里）。
真件 units 包里 Shader(48) 只有 **29 个**（`_work/v3a-cache/shader-census.txt»），
而游戏**没有 mod 覆盖机制** ⇒ 不能新发明，只能把真件那几个**搬**进我们的包。

四条难点，本模块各解一条（读数都来自实测，见 `_work/shader-line/结论.md»）：

1. **按名字找不到** —— 29 个 Shader 的 `m_Name» **全是空串**；真名在 `m_ParsedForm.m_Name»
   （判读走 `material_build.shader_name_of»，逻辑只有一份）。
2. **它自己还有依赖** —— `m_Dependencies» 是 `vector<PPtr>»（包内 fileID=0）⇒ 必须递归复制成
   **闭包**。实测两条链：`HDRP/Lit» -> `Hidden/HDRP/FallbackError» -> `Hidden/Core/FallbackError»
   （3 个）；`Shader Graphs/UnitV3»（28,333,744 B）-> `Hidden/Shader Graph/FallbackError»（2 个）。
3. **pathID 会变** —— 复制进新包后 pathID 重排 ⇒ 闭包内每条 PPtr 都要重映射
   （`builder.remap_pptrs»），漏一条就是悬空。
4. **28 MB 的对象** —— 最大的 Shader 28,333,744 B（`compressedBlob» 是 27,982,200 个 UInt8）：
   读 8.3 s / 写 6.8 s。**重映射是恒等时走 `payload=» 原样搬字节**，一个字节都不重编码。

## 两条 pathID 口径（`id_policy»）

* `renumber»（**默认**）—— 新 pathID = `builder.path_id_for("shader|<解析名>")»
  （确定性、跨源包稳定）；闭包内**真有包内引用**的那条要重编码（重映射过的字段树），
  其余仍是 `payload»。
* `preserve» —— 沿用源 pathID：恒等映射 ⇒ **全部 `payload»**、逐字节照搬
  （与 `unit_bundle.py» 头部第 3 条既有口径同形）。

## 判据（每条都能红）

1. **悬空 PPtr = 0** —— `builder.dangling_pptrs» 把**闭包内每一个**对象都过一遍：
   `payload» 的那些也拿它**解码出来的字段树**过（尺子不因为是 payload 就放过它）；
2. **独立读回逐字节** —— `payload» 对象的对象字节 == 源对象字节；有依赖的那些，
   `compressedBlob» 必须逐字节不变（重映射只动 PPtr，不动 blob）；
3. **闭包内每个 shader 的 blob 能 LZ4 解出且长度 == `decompressedLengths»**（`check_shader_blob»）；
4. 负控（`tests/test_build_shader_from_scratch.py»）：篡改一个字节 / 篡改
   `compressedLengths» / `decompressedLengths» / `offsets» / 一条依赖 PPtr —— 对应那条尺子必须红。

## 与 W1（Unity 工厂）的接缝

`from_unity_bundle(path, name)»：**只读探针**，吃 Unity 构建产物里的 Shader，
先回答「里面有什么」。它**只断言从那个包自己读得出来的东西**（那个包自己的类型树 +
`m_ParsedForm.m_Name»），类型树被剥掉时**抛**（不猜字段名）。复制那一步**不需要新代码**
—— `copy_shaders_from_bundle» 认任何 UnityFS 包，走的是同一条读路。

⚠ W1 还没出包 ⇒ 探针在**真实 Unity 产物**上的读数待补（记在 `_work/shader-line/结论.md»）。

## 复算

    .venv\Scripts\python.exe _work\shader-line\probe_shaders.py     # 只读侦察（冻结件，不开包）
    pytest tests/test_build_shader_from_scratch.py -q
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import builder, clone, material_build
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "ID_POLICIES",
    "ID_POLICY_PRESERVE",
    "ID_POLICY_RENUMBER",
    "MAX_DEPENDENCY_DEPTH",
    "SHADER_CLASS_ID",
    "ShaderBlobCheck",
    "ShaderBuildError",
    "ShaderCopyResult",
    "ShaderFacts",
    "ShaderInventory",
    "UnityShaderProbe",
    "check_shader_blob",
    "copy_shaders_from_bundle",
    "dependency_pptrs_of",
    "from_unity_bundle",
    "non_modifiable_texture_pptrs_of",
    "read_shader_inventory",
    "shader_segments_of",
]

#: Unity classID：Shader（units 里 29 个，真名在 m_ParsedForm.m_Name）。
SHADER_CLASS_ID = 48

#: 依赖链的深度上限（实测最深 2 层：HDRP/Lit -> FallbackError -> Core/FallbackError）。
MAX_DEPENDENCY_DEPTH = 4

#: 给闭包里的对象**重新分配** pathID（默认口径）。
ID_POLICY_RENUMBER = "renumber"
#: 沿用源 pathID（恒等映射 => 一个字节都不重编码）。
ID_POLICY_PRESERVE = "preserve"
ID_POLICIES: tuple[str, ...] = (ID_POLICY_RENUMBER, ID_POLICY_PRESERVE)

_SEED_PREFIX = "shader|"
_RES_S_SUFFIX = ".resS"
_ENCODING_PAYLOAD = "payload"
_ENCODING_FIELDS = "fields"


class ShaderBuildError(ValueError):
    """调用方给错了（**不是** AssetError：这一种是入参/口径错，不是资产层出错）。"""


# --------------------------------------------------------------------------- #
# 字段树上的四条只读判读（全部只按包内类型树给出来的键名，⛔ 不猜字段）
# --------------------------------------------------------------------------- #
def dependency_pptrs_of(fields: Mapping[str, Any]) -> tuple[tuple[int, int], ...]:
    """`m_Dependencies» 里的 PPtr，**原样**给出 `(m_FileID, m_PathID)»（一条不丢、一条不加）。

    真件形状（实测 6/6）：`vector<PPtr>» -> `[{'m_FileID': 0, 'm_PathID': ...}, ...]»。
    认不出就抛 —— 那是资产层自相矛盾，不是可以猜的场合。
    """
    got = fields.get("m_Dependencies")
    if got is None:
        return ()
    if isinstance(got, (str, bytes, bytearray)) or not isinstance(got, Sequence):
        raise ShaderBuildError(
            f"m_Dependencies 不是数组（拿到 {type(got).__name__}）—— 这不是 Shader 的字段树"
        )
    out: list[tuple[int, int]] = []
    for i, item in enumerate(got):
        if not isinstance(item, Mapping):
            raise ShaderBuildError(
                f"m_Dependencies[{i}] 不是 PPtr（拿到 {type(item).__name__}）—— ⛔ 不猜形状"
            )
        out.append((int(item.get("m_FileID", 0)), int(item.get("m_PathID", 0))))
    return tuple(out)


def non_modifiable_texture_pptrs_of(fields: Mapping[str, Any]) -> tuple[tuple[int, int], ...]:
    """`m_NonModifiableTextures» 里的 PPtr，**形状无关**地把它们全挖出来。

    ⛔ 不猜它是 `map» 还是 `list<pair>»：直接走 `builder.iter_pptrs» 遍历那棵子树
    （PPtr 的判据是「键恰好是 m_FileID/m_PathID」，由类型树与 clone 定死）。
    留空的槽名**不解析** —— 本模块不搬这类贴图（真件它们指向 .resS 里的流式贴图）。
    """
    got = fields.get("m_NonModifiableTextures")
    if not got:
        return ()
    return tuple(
        (int(file_id), int(path_id)) for _path, file_id, path_id in builder.iter_pptrs(got)
    )


def _int_groups(value: Any, name: str, where: str) -> list[list[int]]:
    """把 `offsets» / `compressedLengths» / `decompressedLengths» 读成 `list[list[int]]»。

    真件形状（实测 6/6）：`vector<vector<unsigned int>>» -> `[[...]]»（外层组、内层段）。
    """
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ShaderBuildError(f"{where}{name} 不是数组（拿到 {type(value).__name__}）")
    out: list[list[int]] = []
    for i, row in enumerate(value):
        if isinstance(row, (str, bytes, bytearray)) or not isinstance(row, Sequence):
            raise ShaderBuildError(
                f"{where}{name}[{i}] 不是数组 —— 真件形状是 list[list[int]]，⛔ 不猜"
            )
        out.append([int(v) for v in row])
    return out


def shader_segments_of(
    fields: Mapping[str, Any], *, where: str = ""
) -> tuple[tuple[int, int, int], ...]:
    """Shader 的 blob 分段：`(offset, compressedLength, decompressedLength)» 按组序展平。

    三条并行数组（`offsets» / `compressedLengths» / `decompressedLengths»）的**形状必须逐项一致**
    —— 不一致直接抛（资产层自相矛盾，⛔ 不猜）。
    """
    offs = _int_groups(fields.get("offsets"), "offsets", where)
    clens = _int_groups(fields.get("compressedLengths"), "compressedLengths", where)
    dlens = _int_groups(fields.get("decompressedLengths"), "decompressedLengths", where)
    if not len(offs) == len(clens) == len(dlens):
        raise ShaderBuildError(
            f"{where}三条数组的组数不同：offsets {len(offs)} / compressedLengths {len(clens)}"
            f" / decompressedLengths {len(dlens)}"
        )
    segs: list[tuple[int, int, int]] = []
    for g, (o_row, c_row, d_row) in enumerate(zip(offs, clens, dlens, strict=True)):
        if not len(o_row) == len(c_row) == len(d_row):
            raise ShaderBuildError(
                f"{where}第 {g} 组的段数不同：offsets {len(o_row)} / compressedLengths "
                f"{len(c_row)} / decompressedLengths {len(d_row)}"
            )
        segs.extend(zip(o_row, c_row, d_row, strict=True))
    return tuple(segs)


@dataclass(frozen=True, slots=True)
class ShaderBlobCheck:
    """一个 Shader 的 `compressedBlob» 完整性读数（LZ4 逐段解出 + 长度对账）。"""

    path_id: int
    name: str
    blob_bytes: int
    groups: int
    segments: int
    failures: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failures

    def line(self) -> str:
        tag = self.name or f"pathID={self.path_id}"
        state = "OK" if self.ok else f"**红** {len(self.failures)} 条"
        return (
            f"{tag}：blob {self.blob_bytes:,} B / {self.groups} 组 {self.segments} 段 -> {state}"
        )


def check_shader_blob(
    fields: Mapping[str, Any], *, path_id: int = 0, name: str = ""
) -> ShaderBlobCheck:
    """把 `compressedBlob» 按 `offsets/compressedLengths/decompressedLengths» **逐段 LZ4 解**。

    判据：每一段都解得开，且 `len(解出来的) == decompressedLengths[i][j]»。
    ⚠ 长度/结构自相矛盾抛 `ShaderBuildError»（那是字段树坏了）；解不开或长度不符**返回失败行**
    —— 两种都要能被调用方看见，而不是一种抛一种吞。
    """
    try:
        import lz4.block  # noqa: PLC0415 -- 可选依赖：与 builder._lz4hc 同一口径（顶层零第三方）
    except ImportError as exc:  # pragma: no cover - 环境缺 lz4 时才有
        raise AssetError(
            "没装 lz4（python-lz4）⇒ 解不了 Shader 的 compressedBlob",
            hint="仓内 venv 已带：.venv\\Scripts\\python.exe -m pip install lz4",
        ) from exc

    where = f"{name or f'pathID={path_id}'} 的 "
    blob = fields.get("compressedBlob")
    if blob is None:
        raise ShaderBuildError(f"{where}字段树里没有 compressedBlob —— 这不是 Shader 的字段树")
    if isinstance(blob, (bytes, bytearray)):
        raw = bytes(blob)
    elif isinstance(blob, Sequence):
        try:
            # C 级 list[int] -> bytes（2,798 万个元素那个 shader 就靠这一步别变慢）
            raw = bytes(blob)
        except ValueError as exc:
            raise ShaderBuildError(
                f"{where}compressedBlob 里有 0..255 之外的整数：{exc}（不是 vector<UInt8>？）"
            ) from exc
    else:
        raise ShaderBuildError(
            f"{where}compressedBlob 既不是 bytes 也不是数组（拿到 {type(blob).__name__}）"
        )
    segs = shader_segments_of(fields, where=where)
    failures: list[str] = []
    for i, (off, clen, dlen) in enumerate(segs):
        if off < 0 or clen < 0 or off + clen > len(raw):
            failures.append(
                f"第 {i} 段越界：offset {off:,} + compressedLength {clen:,} > blob {len(raw):,} B"
            )
            continue
        try:
            out = lz4.block.decompress(raw[off : off + clen], uncompressed_size=dlen)
        except Exception as exc:  # noqa: BLE001 -- lz4 抛的是 ValueError/RuntimeError 混着，一律记成一行失败
            failures.append(
                f"第 {i} 段解不开（offset {off:,} / {clen:,} B）：{type(exc).__name__}: {exc}"
            )
            continue
        if len(out) != dlen:
            failures.append(f"第 {i} 段长度 {len(out):,} != decompressedLengths {dlen:,}")
    groups = len(_int_groups(fields.get("offsets"), "offsets", where))
    return ShaderBlobCheck(
        path_id=int(path_id),
        name=str(name),
        blob_bytes=len(raw),
        groups=groups,
        segments=len(segs),
        failures=tuple(failures),
    )


# --------------------------------------------------------------------------- #
# 只读事实 / 索引
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ShaderFacts:
    """一个 Shader 对象的**只读事实**（全部来自它自己的字段树，⛔ 不猜）。"""

    path_id: int
    parsed_name: str
    plain_name: str
    byte_size: int
    dependency_pptrs: tuple[tuple[int, int], ...] = ()
    non_modifiable_pptrs: tuple[tuple[int, int], ...] = ()
    blob_bytes: int = 0
    group_segment_counts: tuple[int, ...] = ()
    segment_count: int = 0
    sub_shader_count: int = 0
    keyword_count: int = 0
    fallback_name: str = ""

    @property
    def in_bundle_dependency_ids(self) -> tuple[int, ...]:
        """包内（fileID=0）依赖的 pathID —— 闭包就是沿这一列递归。"""
        return tuple(pid for fid, pid in self.dependency_pptrs if fid == 0 and pid != 0)

    @property
    def cross_package_dependency_pptrs(self) -> tuple[tuple[int, int], ...]:
        """跨包依赖（fileID != 0）—— 本模块**搬不动**，如实报出来。"""
        return tuple((fid, pid) for fid, pid in self.dependency_pptrs if fid != 0 and pid != 0)

    def line(self) -> str:
        return (
            f"pathID {self.path_id} {self.byte_size:,} B 名={self.parsed_name!r}"
            f" 依赖 {len(self.dependency_pptrs)} 条 / blob {self.blob_bytes:,} B"
            f"（{len(self.group_segment_counts)} 组 {self.segment_count} 段）"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_id": self.path_id,
            "parsed_name": self.parsed_name,
            "plain_name": self.plain_name,
            "byte_size": self.byte_size,
            "dependency_pptrs": [list(v) for v in self.dependency_pptrs],
            "non_modifiable_pptrs": [list(v) for v in self.non_modifiable_pptrs],
            "blob_bytes": self.blob_bytes,
            "group_segment_counts": list(self.group_segment_counts),
            "segment_count": self.segment_count,
            "sub_shader_count": self.sub_shader_count,
            "keyword_count": self.keyword_count,
            "fallback_name": self.fallback_name,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ShaderFacts:
        return cls(
            path_id=int(raw["path_id"]),
            parsed_name=str(raw["parsed_name"]),
            plain_name=str(raw["plain_name"]),
            byte_size=int(raw["byte_size"]),
            dependency_pptrs=tuple((int(a), int(b)) for a, b in raw["dependency_pptrs"]),
            non_modifiable_pptrs=tuple(
                (int(a), int(b)) for a, b in raw["non_modifiable_pptrs"]
            ),
            blob_bytes=int(raw["blob_bytes"]),
            group_segment_counts=tuple(int(v) for v in raw["group_segment_counts"]),
            segment_count=int(raw.get("segment_count", 0)),
            sub_shader_count=int(raw["sub_shader_count"]),
            keyword_count=int(raw["keyword_count"]),
            fallback_name=str(raw["fallback_name"]),
        )


@dataclass(frozen=True, slots=True)
class ShaderInventory:
    """源包里**全部** Shader 的索引（解析名 -> pathID + 依赖 + blob 结构）。

    ⚠ 建一次要**解每一个 Shader**（units 29 个 / 最大的那个 8.3 s / 全扫约 23 s）——
    名字只在字段树里（`m_Name» 全是空串），不读就没有别的办法。
    `to_json()» 存下来可复用：第二次 `copy_shaders_from_bundle(inventory=...)» 只解闭包那几个。
    """

    bundle: str
    file_size: int = 0
    seconds: float = 0.0
    facts: tuple[ShaderFacts, ...] = ()

    def by_path_id(self) -> dict[int, ShaderFacts]:
        return {f.path_id: f for f in self.facts}

    def names(self) -> tuple[str, ...]:
        return tuple(f.parsed_name for f in self.facts)

    def resolve(self, name: str) -> ShaderFacts:
        """解析名 -> ShaderFacts；没有 / 重名一律抛（⛔ 不猜一个"最像的"）。"""
        hits = [f for f in self.facts if f.parsed_name == str(name)]
        if not hits:
            known = [n for n in self.names() if n]
            raise AssetError(
                f"{Path(self.bundle).name} 里没有解析名为 {name!r} 的 Shader"
                f"（包内 {len(self.facts)} 个，认得名字的 {len(known)} 个）",
                hint="包内名字：" + ("、".join(known[:12]) or "（一个都没读到名字）"),
            )
        if len(hits) > 1:
            raise AssetError(
                f"{Path(self.bundle).name} 里有 {len(hits)} 个 Shader 都叫 {name!r}"
                f"（pathID {[f.path_id for f in hits]}）—— ⛔ 不猜该搬哪一个"
            )
        return hits[0]

    def to_json(self) -> str:
        return json.dumps(
            {
                "bundle": self.bundle,
                "file_size": self.file_size,
                "seconds": self.seconds,
                "facts": [f.to_dict() for f in self.facts],
            },
            ensure_ascii=False,
            indent=1,
        )

    @classmethod
    def from_json(cls, text: str) -> ShaderInventory:
        raw = json.loads(text)
        return cls(
            bundle=str(raw["bundle"]),
            file_size=int(raw.get("file_size", 0)),
            seconds=float(raw.get("seconds", 0.0)),
            facts=tuple(ShaderFacts.from_dict(item) for item in raw["facts"]),
        )


# --------------------------------------------------------------------------- #
# 包读侧（只读；任何 UnityFS 包都走这一条 —— 真件包与 Unity 构建产物同一条路）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class _SerializedFile:
    node: ufs.Node
    info: ufs.SerializedFileInfo
    table: builder.RawTypeTable

    def class_id_of(self, row: Sequence[int]) -> int:
        type_index = int(row[4])
        if not 0 <= type_index < len(self.table.entries):
            raise AssetError(
                f"对象 pathID={int(row[1])} 的 type_id={type_index} 越出类型表"
                f"（{len(self.table.entries)} 项）—— 对象表读偏了"
            )
        return int(self.table.entries[type_index].class_id)

    def raw_of(self, reader: ufs.UfsReader, row: Sequence[int]) -> bytes:
        start = self.node.offset + self.info.data_offset + int(row[2])
        return bytes(reader.read_range(start, int(row[3])))

    def decode(self, reader: ufs.UfsReader, row: Sequence[int], class_id: int) -> Mapping[str, Any]:
        entry = self.table.entry_for(int(class_id))
        if not entry.nodes:
            raise AssetError(
                f"{Path(str(self.node.path)).name} 的类型表里 classID={class_id} **没有类型树**",
                hint="本模块按包内类型树读字段（⛔ 不猜字段名）；这个包要么重打（带 TypeTree）、"
                     "要么它不是我们能读的那种包",
            )
        return clone.read_object(self.raw_of(reader, row), entry.nodes)


def _open_files(
    reader: ufs.UfsReader, common_strings: Mapping[int, str] | None
) -> tuple[_SerializedFile, ...]:
    """包里**全部** SerializedFile 节点（跳过 .resS —— 那是裸数据流，不是对象表）。

    ⚠ «common_strings» 不传就按 UnityPy 那份取 —— 与 «builder.reopen_container» /
    «unit_bundle.assemble_unit_bundle» 同一口径：类型树里的字段名可能引用**公共串表**
    （格式常量），不传的话连类型表都解不开（实测：本模块的用例第一次跑就是这么红的）。
    """
    if common_strings is None:
        common_strings = clone.unitypy_common_strings()
    out: list[_SerializedFile] = []
    for node in reader.nodes:
        if str(node.path).endswith(_RES_S_SUFFIX):
            continue
        info = ufs.read_serialized_file(reader, node)
        meta = reader.read_range(node.offset, info.data_offset)
        out.append(
            _SerializedFile(
                node=node,
                info=info,
                table=builder.read_raw_type_table(meta, common_strings=common_strings),
            )
        )
    if not out:
        raise AssetError(f"{reader.path} 里没有 SerializedFile 节点（只有 .resS？）")
    return tuple(out)


def _object_index(files: Sequence[_SerializedFile]) -> dict[int, tuple[int, tuple[int, ...]]]:
    """pathID -> (第几个 SerializedFile 节点, 对象表那一行)。重号直接抛（⛔ 不猜）。"""
    index: dict[int, tuple[int, tuple[int, ...]]] = {}
    for i, sf in enumerate(files):
        for row in sf.info.objects:
            pid = int(row[1])
            if pid in index:
                raise AssetError(
                    f"pathID={pid} 在两个 SerializedFile 节点里都出现 —— ⛔ 不猜该用哪一个",
                    hint="先把包拆成一个节点，或者按节点分开处理",
                )
            index[pid] = (i, tuple(int(v) for v in row))
    return index


def _facts_from(fields: Mapping[str, Any], path_id: int, byte_size: int) -> ShaderFacts:
    report = material_build.shader_name_report(fields)
    parsed = fields.get("m_ParsedForm")
    parsed_map = parsed if isinstance(parsed, Mapping) else {}
    blob = fields.get("compressedBlob")
    # 三条数组形状自相矛盾就在这里抛 —— 事实行不许建在坏树上（这个返回值也当场用掉）。
    segs = shader_segments_of(fields, where=f"pathID={path_id} 的 ")
    return ShaderFacts(
        path_id=int(path_id),
        parsed_name=str(report["m_ParsedForm.m_Name"]),
        plain_name=str(report["m_Name"]),
        byte_size=int(byte_size),
        dependency_pptrs=dependency_pptrs_of(fields),
        non_modifiable_pptrs=non_modifiable_texture_pptrs_of(fields),
        blob_bytes=len(blob) if isinstance(blob, Sequence) and not isinstance(blob, str) else 0,
        group_segment_counts=tuple(
            len(row) for row in _int_groups(fields.get("offsets"), "offsets", "")
        ),
        segment_count=len(segs),
        sub_shader_count=len(parsed_map.get("m_SubShaders") or ()),
        keyword_count=len(parsed_map.get("m_KeywordNames") or ()),
        fallback_name=str(parsed_map.get("m_FallbackName") or ""),
    )


def _scan_all(
    files: Sequence[_SerializedFile],
    reader: ufs.UfsReader,
    index: Mapping[int, tuple[int, Any]],
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[ShaderFacts, ...]:
    """把包里每一个 Shader 都解一遍，读成事实行（名字只在字段树里，绕不开）。"""
    ids = [pid for pid, (i, row) in index.items() if files[i].class_id_of(row) == SHADER_CLASS_ID]
    out: list[ShaderFacts] = []
    for n, pid in enumerate(sorted(ids), 1):
        i, row = index[pid]
        fields = files[i].decode(reader, row, SHADER_CLASS_ID)
        facts = _facts_from(fields, pid, int(row[3]))
        out.append(facts)
        if on_progress is not None:
            on_progress(n, len(ids), facts.parsed_name)
    return tuple(out)


# --------------------------------------------------------------------------- #
# 入口 1：名字索引（只读）
# --------------------------------------------------------------------------- #
def read_shader_inventory(
    bundle_path: str | Path,
    *,
    common_strings: Mapping[int, str] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> ShaderInventory:
    """把源包里**每一个** Shader 的解析名 / 依赖 / blob 结构读成一张索引（只读，一个字节不写）。

    ⚠ 代价如实说：units（3.38 GB）全扫约 23 s（最大那个 28 MB 的 shader 自己就 8.3 s）。
    索引可 `to_json()» 存盘复用 —— 之后再 `copy_shaders_from_bundle(inventory=...)» 只解闭包那几个。

    ⛔ 不开包的人用 `_work/w10_ref» 的冻结件（`_work/shader-line/probe_shaders.py»）。
    """
    path = Path(bundle_path)
    started = time.time()
    with ufs.UfsReader(path) as reader:
        files = _open_files(reader, common_strings)
        index = _object_index(files)
        facts = _scan_all(files, reader, index, on_progress=on_progress)
        file_size = int(reader.file_size)
    return ShaderInventory(
        bundle=str(path.resolve()),
        file_size=file_size,
        seconds=time.time() - started,
        facts=facts,
    )


# --------------------------------------------------------------------------- #
# 入口 2：复制闭包 -> extra
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ShaderCopyResult:
    """一次 Shader 搬运的结果：`objects» **就是** `assemble_unit_bundle(extra=...)» 要的那一串。"""

    source: str
    id_policy: str
    objects: tuple[builder.ObjectSpec, ...]
    roots: tuple[tuple[str, int], ...]
    new_id_of: Mapping[int, int]
    facts: tuple[ShaderFacts, ...]
    blobs: tuple[ShaderBlobCheck, ...]
    encodings: tuple[tuple[int, str], ...]
    seconds: float
    report: tuple[str, ...]
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def new_path_ids(self) -> tuple[int, ...]:
        return tuple(int(o.path_id) for o in self.objects)

    @property
    def root_path_ids(self) -> tuple[int, ...]:
        return tuple(pid for _label, pid in self.roots)

    def encoding_of(self, path_id: int) -> str:
        for old, how in self.encodings:
            if int(old) == int(path_id):
                return how
        return ""


def copy_shaders_from_bundle(
    bundle_path: str | Path,
    *,
    names: Sequence[str] = (),
    path_ids: Sequence[int] = (),
    inventory: ShaderInventory | None = None,
    id_policy: str = ID_POLICY_RENUMBER,
    reserved_path_ids: Collection[int] = (),
    common_strings: Mapping[int, str] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> ShaderCopyResult:
    """把 `names»（解析名）/ `path_ids» 点名的 Shader **连依赖闭包**搬进我们的包。

    返回的 `objects» 直接当 `unit_bundle.assemble_unit_bundle(extra=...)» 的一项使用：

        inv = read_shader_inventory(units)                  # 一次，可缓存
        got = copy_shaders_from_bundle(units, names=["HDRP/Lit"], inventory=inv)
        ast = assemble_unit_bundle(meta=..., spec=..., extra=[*got.objects, mesh, material])

    参数
    ----
    names / path_ids
        点名要搬的根（至少给一个）。给 `names» 要**解析名**（`m_ParsedForm.m_Name»，不是空串的
        `m_Name»）；给 `path_ids» 则只解那几个（**不去全扫**），但 pathID 是包内编号、跨包不稳。
    inventory
        `read_shader_inventory» 的结果（可选）。不给而给了 `names» ⇒ 现场全扫一次。
        ⚠ 索引必须**是这个包**建的，换包要重建（不是就抛）。
    id_policy
        `renumber»（默认，新 pathID = `path_id_for("shader|<解析名>")»）
        或 `preserve»（沿用源 pathID）。
    reserved_path_ids
        调用方**自己**已经占掉的 pathID（我们的 mesh / 材质 / 贴图…）—— 撞了就抛，不静默。

    ⚠ 代价（真件 units 实测，2026-10-08）：`HDRP/Lit» 闭包（3 个 / 5.6 MB）**11.6 s**；
    `Shader Graphs/UnitV3» 闭包（2 个 / 28.3 MB）**60.7 s** —— 里面是「解 28 MB + 逐段 LZ4 自证 +
    重映射那一遍深拷贝」。恒等映射时（`id_policy="preserve"»）走 payload，一个字节都不重编码。
    """
    path = Path(bundle_path)
    if not names and not path_ids:
        raise ShaderBuildError("至少要给一个 names 或 path_ids —— 空手没法知道要搬哪一个 shader")
    if id_policy not in ID_POLICIES:
        raise ShaderBuildError(f"id_policy 只能是 {ID_POLICIES}，拿到 {id_policy!r}")
    if inventory is not None and Path(inventory.bundle) != path.resolve():
        raise ShaderBuildError(
            f"这份索引不是 {path} 建的（索引里记的是 {inventory.bundle}）—— "
            "read_shader_inventory 一次、本函数复用那份；换包要重建"
        )
    reserved = {int(v) for v in reserved_path_ids}
    started = time.time()
    problems: list[str] = []
    report: list[str] = []

    with ufs.UfsReader(path) as reader:
        files = _open_files(reader, common_strings)
        index = _object_index(files)
        facts_of: dict[int, ShaderFacts] = dict(inventory.by_path_id()) if inventory else {}

        def facts_for(pid: int) -> ShaderFacts:
            got = facts_of.get(int(pid))
            if got is not None:
                return got
            spot = index.get(int(pid))
            if spot is None:
                raise AssetError(f"pathID={pid} 不在 {path.name} 里（对象表里没有这一条）")
            i, row = spot
            class_id = files[i].class_id_of(row)
            if class_id != SHADER_CLASS_ID:
                raise AssetError(f"pathID={pid} 不是 Shader（classID={class_id}）")
            fields = files[i].decode(reader, row, SHADER_CLASS_ID)
            got = _facts_from(fields, int(pid), int(row[3]))
            facts_of[got.path_id] = got
            return got

        # ---- (1) 根 ------------------------------------------------------------------
        roots: list[tuple[str, int]] = []
        if names:
            inv = inventory
            if inv is None:
                t0 = time.time()
                scanned = _scan_all(files, reader, index, on_progress=on_progress)
                for item in scanned:
                    facts_of.setdefault(item.path_id, item)
                inv = ShaderInventory(
                    bundle=str(path.resolve()),
                    file_size=int(reader.file_size),
                    seconds=time.time() - t0,
                    facts=scanned,
                )
                report.append(f"全扫 {len(scanned)} 个 Shader 建名字索引 {time.time() - t0:.1f}s")
            for name in names:
                hit = inv.resolve(str(name))
                facts_of[hit.path_id] = hit
                roots.append((str(name), hit.path_id))
        for pid in path_ids:
            item = facts_for(int(pid))
            roots.append((item.parsed_name or f"pathID={item.path_id}", item.path_id))

        ordered_roots: list[tuple[str, int]] = []
        seen_root: set[int] = set()
        for label, pid in roots:
            if pid not in seen_root:
                seen_root.add(pid)
                ordered_roots.append((label, pid))

        # ---- (2) 依赖闭包（BFS + 环 + 深度上限） ---------------------------------------
        order: list[int] = []
        seen: set[int] = set()
        queue: deque[tuple[int, int]] = deque((pid, 0) for _label, pid in ordered_roots)
        while queue:
            pid, depth = queue.popleft()
            if pid in seen:
                continue
            if depth > MAX_DEPENDENCY_DEPTH:
                raise AssetError(
                    f"shader 依赖链超过 {MAX_DEPENDENCY_DEPTH} 层（pathID={pid}）",
                    hint="多半是走错了根；真件实测最深 2 层",
                )
            seen.add(pid)
            order.append(pid)
            facts = facts_for(pid)
            for fid, dep in facts.dependency_pptrs:
                if dep == 0:
                    continue
                if fid != 0:
                    problems.append(
                        f"{facts.parsed_name or facts.path_id} 的依赖 (fileID={fid}, pathID={dep})"
                        " 跨包 —— 本模块搬不动（要么把它也复制进来，要么声明 external）"
                    )
                    continue
                if dep not in index:
                    raise AssetError(
                        f"{facts.parsed_name!r} 的依赖 pathID={dep} 不在这个包里",
                        hint="要么它真的跨包（那条路本模块不接），要么对象表读错了；"
                             "⛔ 不静默留一条悬空",
                    )
                queue.append((dep, depth + 1))

        for pid in order:
            facts = facts_of[pid]
            if facts.non_modifiable_pptrs:
                problems.append(
                    f"{facts.parsed_name or facts.path_id} 带 m_NonModifiableTextures "
                    f"{len(facts.non_modifiable_pptrs)} 条（真件它们指向 .resS 里的流式贴图）"
                    "—— 本模块不搬这类贴图 ⇒ 搬进新包必然悬空"
                )

        # ---- (3) 新 pathID -----------------------------------------------------------
        mapping: dict[int, int] = {}
        if id_policy == ID_POLICY_PRESERVE:
            mapping = {pid: pid for pid in order}
        else:
            for pid in order:
                nm = facts_of[pid].parsed_name
                if not nm:
                    raise AssetError(
                        f"pathID={pid} 的 m_ParsedForm.m_Name 是空串 —— 给不出确定性的新 pathID"
                        "（⛔ 不猜）",
                        hint="用 id_policy='preserve' 沿用源 pathID，或者换一个能读出名字的 shader",
                    )
                mapping[pid] = builder.path_id_for(_SEED_PREFIX + nm)
            by_new: dict[int, list[int]] = {}
            for old, new in mapping.items():
                by_new.setdefault(new, []).append(old)
            dup = {new: olds for new, olds in by_new.items() if len(olds) > 1}
            if dup:
                raise ShaderBuildError(
                    "新 pathID 撞了（两个 Shader 落到同一个值）："
                    + "；".join(
                        f"{new} <- " + "、".join(repr(facts_of[o].parsed_name) for o in olds)
                        for new, olds in dup.items()
                    ),
                    + "；名字相同或 crc32 撞了就换 id_policy='preserve'"
                )
        clash = sorted(set(mapping.values()) & reserved)
        if clash:
            raise ShaderBuildError(
                f"新 pathID 与调用方已有的对象撞了：{clash}"
                "（把 reserved_path_ids 传全：我们的 mesh/材质/贴图…，或者换 id_policy）"
            )
        if set(mapping.values()) & {0, 1}:
            raise ShaderBuildError("新 pathID 不许是 0（空引用）或 1（AssetBundle 自己占着）")

        # ---- (4) 逐对象：解码 -> 重映射 -> payload/fields -----------------------------
        changed = {int(o): int(n) for o, n in mapping.items() if int(o) != int(n)}
        objects: list[builder.ObjectSpec] = []
        shadow: list[builder.ObjectSpec] = []
        blobs: list[ShaderBlobCheck] = []
        encodings: list[tuple[int, str]] = []
        moved = 0
        for pid in order:
            facts = facts_of[pid]
            i, row = index[pid]
            raw = files[i].raw_of(reader, row)
            fields = files[i].decode(reader, row, SHADER_CLASS_ID)
            # 只有「这条对象真的引用到了被重编号的 pathID」才需要重编码；
            # 「有没有」由产品自己的 PPtr 普查器说了算（⛔ 不按字段名猜哪里会有引用）。
            needs_remap = bool(changed) and any(
                fid == 0 and target in changed for _p, fid, target in builder.iter_pptrs(fields)
            )
            label = f"shader:{facts.parsed_name or pid}"
            if needs_remap:
                remapped = builder.remap_pptrs(fields, mapping)
                objects.append(
                    builder.ObjectSpec(SHADER_CLASS_ID, remapped, mapping[pid], label=label)
                )
                encodings.append((pid, _ENCODING_FIELDS))
            else:
                remapped = fields
                objects.append(
                    builder.ObjectSpec(
                        SHADER_CLASS_ID, None, mapping[pid], payload=raw, label=label
                    )
                )
                encodings.append((pid, _ENCODING_PAYLOAD))
                moved += len(raw)
            shadow.append(builder.ObjectSpec(SHADER_CLASS_ID, remapped, mapping[pid], label=label))
            blobs.append(check_shader_blob(fields, path_id=pid, name=facts.parsed_name))

        # ---- (5) 尺子 -----------------------------------------------------------------
        dangling = builder.dangling_pptrs(shadow)
        problems.extend(dangling)
        problems.extend(
            f"{b.name or b.path_id} 的 compressedBlob 完整性：{line}"
            for b in blobs
            for line in b.failures
        )

    seconds = time.time() - started
    report.append(
        f"闭包 {len(order)} 个 Shader（根 {len(ordered_roots)} 个）· id_policy={id_policy}"
        f" · payload {sum(1 for _p, how in encodings if how == _ENCODING_PAYLOAD)} 个"
        f"（{moved:,} B 逐字节搬）"
        f" · fields {sum(1 for _p, how in encodings if how == _ENCODING_FIELDS)} 个"
    )
    report.append(
        f"悬空普查：闭包内 {len(order)} 个对象全过一遍（payload 的也用它解码出来的字段树）"
        f" -> {len(dangling)} 条"
    )
    report.extend("blob · " + b.line() for b in blobs)
    report.append(f"耗时 {seconds:.1f}s")
    return ShaderCopyResult(
        source=str(path.resolve()),
        id_policy=str(id_policy),
        objects=tuple(objects),
        roots=tuple((label, mapping[pid]) for label, pid in ordered_roots),
        new_id_of=dict(mapping),
        facts=tuple(facts_of[pid] for pid in order),
        blobs=tuple(blobs),
        encodings=tuple(encodings),
        seconds=seconds,
        report=tuple(report),
        problems=tuple(problems),
    )


# --------------------------------------------------------------------------- #
# 入口 3：W1 接缝 —— 吃 Unity 构建产物里的 Shader（**只读探针**）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class UnityShaderProbe:
    """一个 Unity 构建产物包里的 Shader 读数（只读探针的输出）。"""

    bundle: str
    name: str
    facts: ShaderFacts
    blob: ShaderBlobCheck
    inventory: ShaderInventory

    @property
    def ok(self) -> bool:
        return self.blob.ok and not self.facts.cross_package_dependency_pptrs

    def lines(self) -> tuple[str, ...]:
        out = [
            f"包 {Path(self.bundle).name}（{self.inventory.file_size:,} B，"
            f"{len(self.inventory.facts)} 个 Shader）",
            "  " + self.facts.line(),
            "  可用名字："
            + ("、".join(n for n in self.inventory.names() if n) or "（一个都没读到）"),
            "  " + self.blob.line(),
        ]
        if self.facts.cross_package_dependency_pptrs:
            out.append(f"  ⚠ 跨包依赖：{self.facts.cross_package_dependency_pptrs}")
        return tuple(out)


def from_unity_bundle(
    bundle_path: str | Path,
    name: str,
    *,
    common_strings: Mapping[int, str] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> UnityShaderProbe:
    r"""W1 接缝：**只读探针** —— 吃 Unity 构建产物里的 Shader，先回答「里面有什么」。

    ⛔ **不猜字段**：一切读数都来自**那个包自己的类型树**（`builder.read_raw_type_table»）
    与 `m_ParsedForm.m_Name»。类型树被剥掉（Unity 侧 `DisableWriteTypeTree»）时**抛**，
    而不是拿别的包的树去套。

    ⚠ **实测待补**：unity-factory 还没出包 ⇒ 本函数目前只在**我们自己造的 UnityFS 包**
    （真件对象字节 + 真件类型表）上验过。W1 出包后跑：

        .venv\Scripts\python.exe -c "from ba_mod_maker.asset_kernel import shader_build as S; \
            print(chr(10).join(S.from_unity_bundle(r'<W1 的包>', '<名字>').lines()))"

    复制那一步**不需要新代码** —— `copy_shaders_from_bundle» 认任何 UnityFS 包，走同一条读路。
    """
    path = Path(bundle_path)
    started = time.time()
    with ufs.UfsReader(path) as reader:
        files = _open_files(reader, common_strings)
        index = _object_index(files)
        facts = _scan_all(files, reader, index, on_progress=on_progress)
        inv = ShaderInventory(
            bundle=str(path.resolve()),
            file_size=int(reader.file_size),
            seconds=time.time() - started,
            facts=facts,
        )
        target = inv.resolve(str(name))
        i, row = index[target.path_id]
        fields = files[i].decode(reader, row, SHADER_CLASS_ID)
        blob = check_shader_blob(fields, path_id=target.path_id, name=target.parsed_name)
    return UnityShaderProbe(
        bundle=str(path.resolve()), name=str(name), facts=target, blob=blob, inventory=inv
    )
