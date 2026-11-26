"""反向 Δ：安装的可逆性（ADR 0007 第 4 条 · 用户逐条拍板）。

## 为什么不是「拷整包当备份」

用户原话：「不要帮我手动备份，会生成很多的垃圾备份文件占用我的硬盘空间」。
旧写回器（writer._write_bundle）的可逆性**只有整包副本这一条**：units 3.22 GB、
data.unity3d 7.1 GB —— 装一次就多一份同量级垃圾。

容器 = 「头 ＋ 块表」＋ 各块压缩字节，而写回只重压**被碰到的那几块**、其余**原样搬**
（ADR 0006 的 Δ 级重切）⇒ 还原只需要四样（ADR 0007 第 4 条）：

    目标路径 ＋ 装前/装后 sha256 ＋ 原 [0, data_start) 字节 ＋ {块号: 该块原始压缩字节}

量级（实测见 _work/delta_roundtrip.py）：一个 8 B 的对象改动，补丁 = 头（几十~几百 KB）
＋ 命中块（1~3 块 × ≤128 KB）。

## 三种目标

* BundleTarget —— UnityFS 容器。**逐块记**：原文件的每一个块，要么把压缩字节存进补丁 blob，
  要么记「它的字节还在当前文件的第 K 块（第 off 字节处）」—— 就是原样搬过去的那些。
* FileTarget —— 整文件（catalog.json / BA_Mod.zip）。存原字节；装前不存在的记
  existed=False，还原 = 删掉它（「删文件即卸载」，ADR 0007 第 3 条）。
* RangeTarget —— 容器里**一段解压流区间**（data.unity3d 那种 6.63 GB 的容器，一次只改
  里面一颗对象的那条链）。存「那一段的原始字节 ＋ 节点名 ＋ pathID ＋ 原始/改后长度」，
  还原时按 pathID **重新定位**、再用同一套选择性写回把原字节放回原位。
  补丁体积 = 那一段那么大（实测一颗 UserItemsConfig 220,220 B），⛔ 不是整个容器。

## 「再装覆盖它」= 合并，不是丢弃

第二次安装**不许**把基线改成「第一次装完的样子」—— 那样还原只能回到半改状态。
PatchBuilder 会把已有补丁并进来：**基线永远是「用户装之前那一刻」**。
已经存过字节的块保持不动；还指在当前文件里的块，这次要被重压就把当前字节存下来，
还走原样搬就更新它的落点。⛔ 基线对不上（当前文件 sha ≠ 补丁的 sha_after）时**拒绝合并**，
必须先还原或显式丢弃 —— 防的是「拿别人的改动当基线」。

## 还原前的两把锁

1. sha256(当前文件) == 补丁的 sha_after —— 不等就**拒绝自动还原**（ADR 0007 第 4 条：
   防误伤用户后来的改动）。当前 sha == sha_before 时视为**已经还原**（幂等，不报错）。
2. 重建出来的旧文件 sha256 == 补丁的 sha_before —— 不等就**不替换**（一个字节都不落盘）。

判据一行（判定性实验）：还原后整文件 sha256 == 改前；正控 = 还原前手改 1 字节 ⇒ 必须拒绝。
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "FORMAT_VERSION",
    "PATCH_BLOB_NAME",
    "PATCH_DIR_NAME",
    "PATCH_JSON_NAME",
    "BlobRef",
    "BlockRecord",
    "BundleTarget",
    "FilePart",
    "FileTarget",
    "InstallPatch",
    "PatchBuilder",
    "PatchStore",
    "RANGE_FORMAT_VERSION",
    "RangeTarget",
    "ReplaceBlockedError",
    "RestoreReport",
    "SUPPORTED_FORMATS",
    "assert_same_drive",
    "patch_store_root",
    "replace_with_retry",
    "sha256_of",
    "verbatim_placement",
]

#: 补丁柜：工程目录下的 patches/（固定文件名，**覆盖式**，还原成功即删）。
PATCH_DIR_NAME = "patches"
PATCH_JSON_NAME = "install.rmod.json"
PATCH_BLOB_NAME = "install.rmod.bin"

#: 补丁文件格式版本。**写出去**用哪个：柜子里有 RangeTarget 就写 RANGE_FORMAT_VERSION。
FORMAT_VERSION = 2
#: 含 RangeTarget 的补丁格式。老版本产品读到 3 会**明确拒绝**（不会把 range 当成
#: FileTarget 读错）—— 这就是它存在的意义。
RANGE_FORMAT_VERSION = 3
#: 本代码**能读**的格式。⛔ 不在表里一律拒绝（不猜、不回落）。
#: ⚠ 2 与 3 并存是刻意的：加一种 target **不许**让既有的 v2 补丁作废
#: （本机 patches/ 里就躺着一条 v2，见 _work/… 与仓根 patches/）。
SUPPORTED_FORMATS: tuple[int, ...] = (FORMAT_VERSION, RANGE_FORMAT_VERSION)

_READ_CHUNK = 8 << 20

#: 整文件目标（catalog.json / BA_Mod.zip）做差异时的块大小。
#: 实测（_work/probe_catalog_diff.py）：真 catalog 改一条 m_Crc 只差 **10 B**，落在 1 个 4 KB 块里
#: ⇒ 不存整份 4 MB，只存差异块（ADR 0007 第 4 条那句「几十字节」就是这个量级）。
_FILE_CHUNK = 4096


# --------------------------------------------------------------------------- #
# 工具：sha256 / 同盘守卫 / 原子替换
# --------------------------------------------------------------------------- #
def sha256_of(path: str | Path, *, chunk: int = _READ_CHUNK) -> str:
    """一个文件的 sha256（分块读，不把文件读进内存）。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            data = handle.read(chunk)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _drive_of(path: Path) -> str:
    try:
        resolved = path.resolve()
    except OSError:  # pragma: no cover -- 极端路径
        resolved = path.absolute()
    return os.path.splitdrive(str(resolved))[0].upper()


def assert_same_drive(target: Path, game_root: str | Path) -> None:
    """补丁柜必须**跟随游戏所在盘**（红线，与 writer._assert_same_drive 同一口径）。

    历史教训：备份落 C 盘白占 6.95 GB。本模块在 writer 的下游，而 writer 反过来 import 本模块
    ⇒ 这条守卫只能住在这里（一份实现，writer 那边转调它）。
    """
    target_drive = _drive_of(target)
    game_drive = _drive_of(Path(game_root))
    if not target_drive or target_drive != game_drive:
        raise AssetError(
            f"补丁柜在 {target_drive or '?'} 盘，游戏在 {game_drive or '?'} 盘：{target}",
            hint="⛔ 补丁 / 工程目录必须跟随游戏所在盘（不许落 C 盘）",
        )


def patch_store_root(project_root: str | Path | None) -> Path:
    """补丁柜落点：<工程目录>/patches。"""
    base = Path(project_root) if project_root is not None else Path.cwd()
    return base / PATCH_DIR_NAME


class ReplaceBlockedError(AssetError):
    """替换被占用 / 拒绝访问，退避重试到头也没成。

    带上最后那一次的 OSError（`.last`）：writer 那份是**转调**本实现的，而它按
    isinstance(exc, PermissionError) 分流（写回失败时的话术与回滚）—— 原始错误不能被吞掉。
    """

    def __init__(self, dst: Path | str, last: OSError | None, *, attempts: int) -> None:
        super().__init__(
            f"替换 {Path(dst).name} 失败（{int(attempts)} 次都没成）：{last}",
            hint="① 游戏在跑？② 读源句柄没关？两者都要先关掉再写",
        )
        #: 最后一次的原始 OSError（调用方可以按 winerror 分流）。
        self.last = last


def replace_with_retry(
    src: str | Path,
    dst: str | Path,
    *,
    attempts: int = 6,
    pause: float = 0.5,
    collect: bool = True,
) -> None:
    """原子替换（同目录 rename）带 **GC 抖动 + 线性退避重试**。

    ## 为什么每次尝试之前都要 gc.collect()（这就是本函数存在的全部理由）

    UnityPy / fsspec 的读句柄**挂在引用环上**：AssetSource.close() 只丢引用，
    refcount 归零也不释放 —— 只有 gc.collect() 放得开。实测（_work/meshline/retry_probe.py，
    21.6 MB 真包副本，本机 2026-10-12）：没读过包 ⇒ OK；刚读过、不 GC ⇒
    PermissionError winerror=5；GC 之后再试 ⇒ OK。于是「不 GC 只睡」的实现在真件上
    6 次全撞 WinError 5 —— 症状是**写得起、撤不掉**（B 线在真件用例里撞到，task-5 收口）。

    ⛔ 句柄**还活着**（调用方没关读源 / 游戏在跑）的情况重试救不了 —— 那时如实报忙。

    ⛔ **实现只有这一份**：writer._replace_with_retry 转调它（本模块在 writer 的下游，
    而 writer 反过来 import 本模块 ⇒ 只能住这里，口径同 assert_same_drive）。

    :param attempts: 尝试次数（含第一次）。
    :param pause: 第 n 次失败后的等待 = pause × (n + 1)（线性退避）。
    :param collect: 每次尝试前先 gc.collect()。只有用例拿它当负控才关。
    :raises ReplaceBlockedError: 占用类错误（WinError 5 / 32 ⇒ PermissionError）重试到头；
        `.last` 是最后那一次的 OSError。
    :raises OSError: 非占用类错误（例：源不存在）**原样抛出、不重试、不睡**。
    """
    last: OSError | None = None
    tries = max(1, int(attempts))
    for attempt in range(tries):
        if collect:
            gc.collect()
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:  # WinError 5 / 32 在 CPython 里都是 PermissionError
            last = exc
            time.sleep(max(0.0, float(pause)) * (attempt + 1))
    raise ReplaceBlockedError(dst, last, attempts=tries)


def _replace_with_retry(src: Path, dst: Path, *, attempts: int = 6) -> None:
    """本模块内部用的老名字（8 处调用点）—— 转调 replace_with_retry。

    ⚠ 与改前**逐字同语义**：重试到头报 AssetError（现在那个类型是 ReplaceBlockedError）。
    """
    replace_with_retry(src, dst, attempts=attempts)


# --------------------------------------------------------------------------- #
# 补丁的数据结构
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class BlobRef:
    """一段字节在补丁 blob（install.rmod.bin）里的位置。"""

    offset: int
    length: int

    def to_json(self) -> dict[str, int]:
        return {"offset": self.offset, "length": self.length}

    @staticmethod
    def from_json(raw: Mapping[str, Any]) -> BlobRef:
        return BlobRef(int(raw["offset"]), int(raw["length"]))


@dataclass(slots=True)
class BlockRecord:
    """原文件里的**一个块**：压缩字节要么在 blob 里，要么还在当前文件的第 location 块。

    ⚠ 这两个字段**不进 JSON**（见 BundleTarget 的紧凑编码）：
    * compressed —— 存下来的块由 blob 长度自带；原样搬的块由**当前文件的块表**给；
    * location   —— 一串「连续块、同一个平移量」的区间（verbatim_runs）就能表达。
    """

    index: int
    compressed: int = 0
    blob: BlobRef | None = None
    #: 当前（sha_after）文件里的块下标：还原时按它的块表取字节，合并时按它找新落点。
    location: int | None = None

    @property
    def stored(self) -> bool:
        return self.blob is not None


def _copy_target(target: BundleTarget | FileTarget) -> BundleTarget | FileTarget:
    """深拷一份目标记录（合并时不许原地改到盘上那条补丁的对象）。"""
    if isinstance(target, BundleTarget):
        return BundleTarget.from_json(target.to_json())
    if isinstance(target, RangeTarget):
        return RangeTarget.from_json(target.to_json())
    return FileTarget.from_json(target.to_json())


@dataclass(slots=True)
class BundleTarget:
    """一个 UnityFS 容器的反向 Δ。"""

    path: Path
    sha_before: str
    size_before: int
    data_start: int
    header: BlobRef
    blocks: list[BlockRecord] = field(default_factory=list)
    sha_after: str = ""
    size_after: int = 0
    kind: str = "bundle"

    @property
    def stored_bytes(self) -> int:
        total = self.header.length
        total += sum(0 if r.blob is None else r.blob.length for r in self.blocks)
        return total

    def verbatim_runs(self) -> list[tuple[int, int, int]]:
        """把「原样搬」的块压成 [(lo, hi, shift)] —— 区间内 location == index + shift。

        L1 那条链只有**一段**（shift == 0）；Δ 链是「插入点前 0、插入点后 +k」两段。
        这就是把 41,440 条块记录压成几十字节的那一手。
        """
        runs: list[list[int]] = []
        for record in self.blocks:
            if record.blob is not None or record.location is None:
                continue
            shift = record.location - record.index
            if runs and runs[-1][1] == record.index and runs[-1][2] == shift:
                runs[-1][1] = record.index + 1
            else:
                runs.append([record.index, record.index + 1, shift])
        return [(lo, hi, shift) for lo, hi, shift in runs]

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": str(self.path),
            "sha_before": self.sha_before,
            "sha_after": self.sha_after,
            "size_before": self.size_before,
            "size_after": self.size_after,
            "data_start": self.data_start,
            "block_count": len(self.blocks),
            "header": self.header.to_json(),
            "stored": [
                {"index": r.index, "blob": r.blob.to_json()} for r in self.blocks if r.stored
            ],
            "verbatim_runs": [
                {"lo": lo, "hi": hi, "shift": shift} for lo, hi, shift in self.verbatim_runs()
            ],
        }

    @staticmethod
    def from_json(raw: Mapping[str, Any]) -> BundleTarget:
        count = int(raw["block_count"])
        stored = {int(item["index"]): BlobRef.from_json(item["blob"]) for item in raw["stored"]}
        runs = [
            (int(item["lo"]), int(item["hi"]), int(item["shift"]))
            for item in raw.get("verbatim_runs", ())
        ]

        def shift_at(index: int) -> int | None:
            for lo, hi, shift in runs:
                if lo <= index < hi:
                    return shift
            return None

        blocks: list[BlockRecord] = []
        for index in range(count):
            blob = stored.get(index)
            if blob is not None:
                # 存下来的块：长度**自带**（blob 有多长，原块就有多长）—— 不用另外记。
                blocks.append(BlockRecord(index=index, compressed=blob.length, blob=blob))
                continue
            shift = shift_at(index)
            if shift is None:
                raise AssetError(
                    f"补丁里第 {index} 块既没存字节、也不在任何 verbatim 段里",
                    hint="补丁损坏（紧凑编码的两张表对不上）",
                )
            blocks.append(BlockRecord(index=index, location=index + shift))
        return BundleTarget(
            path=Path(raw["path"]),
            sha_before=str(raw["sha_before"]),
            sha_after=str(raw["sha_after"]),
            size_before=int(raw["size_before"]),
            size_after=int(raw["size_after"]),
            data_start=int(raw["data_start"]),
            header=BlobRef.from_json(raw["header"]),
            blocks=blocks,
        )


@dataclass(frozen=True, slots=True)
class FilePart:
    """旧文件里的一段：还原时把它放回 offset 处。"""

    offset: int
    blob: BlobRef

    def to_json(self) -> dict[str, Any]:
        return {"offset": self.offset, "blob": self.blob.to_json()}

    @staticmethod
    def from_json(raw: Mapping[str, Any]) -> FilePart:
        return FilePart(int(raw["offset"]), BlobRef.from_json(raw["blob"]))


def _apply_parts(
    data: bytes, parts: Sequence[FilePart], blob_of: Callable[[BlobRef], bytes]
) -> bytes:
    """把几段旧字节盖回一份数据上 —— 合并基线与还原共用这一个口径。"""
    out = bytearray(data)
    for part in parts:
        chunk = blob_of(part.blob)
        out[part.offset : part.offset + len(chunk)] = chunk
    return bytes(out)


def _run_to_rechunk(
    reader: ufs.UfsReader, target: RangeTarget, start: int, size: int
) -> tuple[int, int]:
    """还原那一趟要按哪个边界重切（**装后**的解压流坐标）。

    ① **新补丁**（记了重切段）⇒ 直接用 —— 那是装的那一刻真的重切过的那一段；
    ② **老补丁**（0/0，修复前写的）⇒ 按 128 KiB 网格把装前那一段的尾巴推出来：
       段尾 = 装前对象末尾所在块的块尾（原始网格）＋ 这一版相对装前的**净**长度变化。
       净变化用「当前 size − 装前 old_size」是对的：前面几次安装的平移互相抵消。

    ⚠ 网格假设：本游戏容器的块是 131072 一块（实测）。**推导错了也不会写坏文件** ——
    还原最后那道 sha256 闸是 fail-closed 的（对不上就一个字节都不落盘）。
    """
    if target.run_end > target.run_start:
        return target.run_start, target.run_end
    lo = ufs.block_start_of(reader, start)
    end = target.stream_start + target.old_size
    steps = -(-(end - lo) // ufs.RECHUNK_SIZE)
    return lo, lo + steps * ufs.RECHUNK_SIZE + (size - target.old_size)


@dataclass(slots=True)
class FileTarget:
    """一个**整文件**的反向 Δ（catalog.json / BA_Mod.zip 这类小文件）。

    存的是**旧文件里与当前文件不同的那些段**（4 KB 对齐的差异块）；长度会变时回退成整份。
    """

    path: Path
    existed: bool
    sha_before: str
    size_before: int
    sha_after: str = ""
    size_after: int = 0
    parts: list[FilePart] = field(default_factory=list)
    #: True = parts 就是整份旧文件（长度变了时的回退口径）。
    whole: bool = False
    kind: str = "file"

    @property
    def stored_bytes(self) -> int:
        return sum(part.blob.length for part in self.parts)

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": str(self.path),
            "existed": self.existed,
            "sha_before": self.sha_before,
            "sha_after": self.sha_after,
            "size_before": self.size_before,
            "size_after": self.size_after,
            "whole": self.whole,
            "parts": [part.to_json() for part in self.parts],
        }

    @staticmethod
    def from_json(raw: Mapping[str, Any]) -> FileTarget:
        return FileTarget(
            path=Path(raw["path"]),
            existed=bool(raw["existed"]),
            sha_before=str(raw["sha_before"]),
            sha_after=str(raw["sha_after"]),
            size_before=int(raw["size_before"]),
            size_after=int(raw["size_after"]),
            whole=bool(raw.get("whole", False)),
            parts=[FilePart.from_json(p) for p in raw.get("parts", ())],
        )


@dataclass(slots=True)
class RangeTarget:
    """容器里**一段解压流区间**的反向 Δ（一次只改容器里一颗对象的那条链专用）。

    与 BundleTarget 的分工（⛔ 不是同一件事，别混）：

    * BundleTarget 记**整容器**（原头 ＋ 逐块压缩字节）—— 还原时不再解析容器；
    * RangeTarget 记**一段区间**：容器路径 ＋ 节点名 ＋ pathID ＋ 那一段的原始字节
      ＋ 原始 / 改后长度。还原时按 pathID **重新定位**（⛔ 不信任记下来的偏移），
      再用同一套选择性写回 ufs.rewrite_container 把原字节放回原位。

    为什么需要它：data.unity3d 整文件 6.63 GB，而写一次的落点**只有那一颗对象**
    （实测 UserItemsConfig 220,220 B）。补丁体积 = len(原始字节)，⛔ 不拷整包（ADR 0007）。
    代价：还原要再走一遍容器写回（真件那 4 分钟档）—— 比整容器 Δ 慢一趟，
    换来的是一条能落进补丁柜、**事后**（另起一个进程）也读得回来的退路。

    ⚠ stream_start 只是**读数**（装前那一刻它在解压流里的起点）；还原一律按 pathID 重新定位，
    因为前面但凡有对象变长，后面所有对象的起点都会平移。
    """

    path: Path
    node_path: str
    path_id: int
    stream_start: int
    old_size: int
    new_size: int
    old_bytes: BlobRef
    label: str = ""
    sha_before: str = ""
    sha_after: str = ""
    size_before: int = 0
    size_after: int = 0
    #: 装的那一刻**被重切**的那一段（解压流坐标，**装后**坐标），闭开区间 [run_start, run_end)。
    #: 由 `ufs.rewrite_run()` 在写回前算出（与 rewrite_container 算 dirty run 同一套算式）。
    #: ⚠ 还原时必须把它交给 `rewrite_container(extra_runs=...)` 复现同一条重切边界 ——
    #: 不复现的话，装后文件的**块边界**与原始文件不同（尾巴多一个 100 B 的块之类），
    #: 整文件 sha256 永远回不去 ⇒ 「撤不掉」。这就是 2026-10-11 的 BUG-2。
    #: 0/0 = 老补丁（记这条之前写的）⇒ 还原退回旧行为（长度没变也能逐位回）。
    run_start: int = 0
    run_end: int = 0
    kind: str = "range"

    @property
    def stored_bytes(self) -> int:
        return self.old_bytes.length

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": str(self.path),
            "node_path": self.node_path,
            "path_id": self.path_id,
            "stream_start": self.stream_start,
            "old_size": self.old_size,
            "new_size": self.new_size,
            "old_bytes": self.old_bytes.to_json(),
            "label": self.label,
            "sha_before": self.sha_before,
            "sha_after": self.sha_after,
            "size_before": self.size_before,
            "size_after": self.size_after,
            "run_start": self.run_start,
            "run_end": self.run_end,
        }

    @staticmethod
    def from_json(raw: Mapping[str, Any]) -> RangeTarget:
        return RangeTarget(
            path=Path(raw["path"]),
            node_path=str(raw["node_path"]),
            path_id=int(raw["path_id"]),
            stream_start=int(raw.get("stream_start", 0)),
            old_size=int(raw["old_size"]),
            new_size=int(raw["new_size"]),
            old_bytes=BlobRef.from_json(raw["old_bytes"]),
            label=str(raw.get("label", "")),
            sha_before=str(raw["sha_before"]),
            sha_after=str(raw["sha_after"]),
            size_before=int(raw["size_before"]),
            size_after=int(raw["size_after"]),
            run_start=int(raw.get("run_start", 0)),
            run_end=int(raw.get("run_end", 0)),
        )


Target = BundleTarget | FileTarget | RangeTarget


@dataclass(slots=True)
class RestoreReport:
    """一次还原的读数。"""

    label: str = ""
    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(self, entry: Mapping[str, Any]) -> None:
        self.entries.append(dict(entry))

    @property
    def ok(self) -> bool:
        return all(bool(e.get("ok")) for e in self.entries)

    def lines(self) -> tuple[str, ...]:
        out = [f"[还原] {self.label or '（无标签）'}"]
        for entry in self.entries:
            mark = "✓" if entry.get("ok") else "✗"
            out.append(f"  {mark} {entry.get('detail', entry.get('path', ''))}")
        return tuple(out)


@dataclass(slots=True)
class InstallPatch:
    """**一次安装**的反向 Δ 记录（可含多个目标）。"""

    blob: bytes
    targets: list[Target]
    label: str = ""
    created: str = ""
    game_root: str = ""
    format: int = FORMAT_VERSION
    #: 最近一次 restore() 里**确实回到装前**的目标下标（不进 JSON）。
    #: PatchStore.restore() 按它剪枝 —— 半次还原之后，已经回去的那几条不许再留在柜里
    #: （BUG-3：留着会让下一次写被 stale-guard 拦，而 discard 会连没还原的一起丢）。
    restored: list[int] = field(default_factory=list, compare=False, repr=False)

    # ---------------- 读盘 / 落盘 ----------------
    @staticmethod
    def load(json_path: str | Path, blob_path: str | Path) -> InstallPatch:
        raw = json.loads(Path(json_path).read_text(encoding="utf-8"))
        version = int(raw.get("format", 0))
        if version not in SUPPORTED_FORMATS:
            raise AssetError(
                f"补丁格式版本 {version} 不在本代码能读的 "
                f"{list(SUPPORTED_FORMATS)} 里：{json_path}",
                hint="旧补丁请用当时那版产品还原；⛔ 不要拿新代码去解释旧补丁",
            )
        blob = Path(blob_path).read_bytes()
        want = str(raw.get("blob_sha256", ""))
        if want and _sha256_bytes(blob) != want:
            raise AssetError(
                f"补丁 blob 自身 sha256 对不上：{blob_path}",
                hint="补丁被改过 / 拷坏过；⛔ 不要用它还原",
            )
        targets: list[Target] = []
        for item in raw["targets"]:
            kind = str(item.get("kind", "file"))
            if kind == "bundle":
                targets.append(BundleTarget.from_json(item))
            elif kind == "range":
                targets.append(RangeTarget.from_json(item))
            elif kind == "file":
                targets.append(FileTarget.from_json(item))
            else:
                raise AssetError(
                    f"补丁里有一个本代码不认识的 kind={kind!r}：{json_path}",
                    hint="补丁来自更新版本的产品；⛔ 不要拿这份代码去还原它",
                )
        return InstallPatch(
            blob=blob,
            targets=targets,
            label=str(raw.get("label", "")),
            created=str(raw.get("created", "")),
            game_root=str(raw.get("game_root", "")),
            # ⚠ 必须把**读到的那版格式**带回来：不带就默默退回默认的 2，
            #    含 range 的补丁在内存里会被当成 v2（用例 test_range_target_is_written_as_format_3
            #    之外的地方全看不出来 —— 2026-10-12 真踩过）。
            format=version,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "created": self.created,
            "label": self.label,
            "game_root": self.game_root,
            "blob_length": len(self.blob),
            "blob_sha256": _sha256_bytes(self.blob),
            "targets": [t.to_json() for t in self.targets],
        }

    def keep_only(self, indices: Sequence[int]) -> InstallPatch:
        """只留这几个目标（部分还原之后把已经回到装前的那几条从柜里去掉）。

        blob **原样留着**：⛔ 不做搬运（少一段复制逻辑就少一处出错面）；被丢掉的那几条
        占的字节留在 blob 里，只影响体积、不影响正确性。
        """
        keep = [self.targets[i] for i in sorted({int(i) for i in indices})]
        return InstallPatch(
            blob=self.blob,
            targets=keep,
            label=self.label,
            created=self.created,
            game_root=self.game_root,
            format=self.format,
        )

    def save(self, json_path: str | Path, blob_path: str | Path) -> None:
        """把这条补丁**原子**写回柜里（与 PatchBuilder.commit 同一套顺序：先 blob 后 json）。"""
        json_p = Path(json_path)
        blob_p = Path(blob_path)
        blob_tmp = blob_p.with_name(blob_p.name + ".tmp")
        json_tmp = json_p.with_name(json_p.name + ".tmp")
        blob_tmp.write_bytes(self.blob)
        json_tmp.write_text(
            json.dumps(self.to_json(), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        _replace_with_retry(blob_tmp, blob_p)
        _replace_with_retry(json_tmp, json_p)

    # ---------------- 读数 ----------------
    @property
    def stored_bytes(self) -> int:
        return sum(t.stored_bytes for t in self.targets)

    def lines(self) -> tuple[str, ...]:
        out = [f"[补丁] {self.created} · {self.label or '（无标签）'}"]
        for target in self.targets:
            if isinstance(target, BundleTarget):
                stored = sum(1 for r in target.blocks if r.stored)
                out.append(
                    f"  {target.path.name}：{target.size_before:,} → {target.size_after:,} B，"
                    f"记下 {stored:,}/{len(target.blocks):,} 块的原始压缩字节"
                    f"（补丁 {target.stored_bytes:,} B）"
                )
            elif isinstance(target, RangeTarget):
                out.append(
                    f"  {target.label or '区间'} · {target.path.name}"
                    f"（{target.node_path} pathID={target.path_id}）"
                    f"：那一段 {target.old_size:,} → {target.new_size:,} B；"
                    f"容器 {target.size_before:,} → {target.size_after:,} B；"
                    f"补丁存原始字节 {target.stored_bytes:,} B"
                )
            else:
                what = "装前不存在（还原 = 删掉它）" if not target.existed else "存了原字节"
                out.append(
                    f"  {target.path.name}：{what}；{target.size_before:,} → "
                    f"{target.size_after:,} B（补丁 {target.stored_bytes:,} B）"
                )
        out.append(f"  补丁体积合计 {self.stored_bytes:,} B（blob {len(self.blob):,} B）")
        return tuple(out)

    # ---------------- 还原 ----------------
    def restore(self, *, check_sha: bool = True) -> RestoreReport:
        report = RestoreReport(label=self.label)
        # 每还原成功一个就登记下标（部分成功 / 中途抛异常时，PatchStore 按它剪枝）。
        self.restored = []
        # range 目标按**容器**分组：同一个容器上的几段区间合并成一趟（各自走一趟的话，
        # 第二段拿到的当前 sha 已经被第一段改掉了，两把锁会互相打架）。
        ranges: dict[str, list[tuple[int, RangeTarget]]] = {}
        for index, target in enumerate(self.targets):
            if isinstance(target, RangeTarget):
                ranges.setdefault(os.path.normcase(str(target.path)), []).append((index, target))
                continue
            if isinstance(target, BundleTarget):
                entry = self._restore_bundle(target, check_sha=check_sha)
            else:
                entry = self._restore_file(target, check_sha=check_sha)
            report.add(entry)
            if entry.get("ok"):
                self.restored.append(index)
        for group in ranges.values():
            entry = self._restore_ranges([t for _i, t in group], check_sha=check_sha)
            report.add(entry)
            if entry.get("ok"):
                self.restored.extend(index for index, _t in group)
        return report

    def _blob_slice(self, ref: BlobRef) -> bytes:
        end = ref.offset + ref.length
        if ref.offset < 0 or end > len(self.blob):
            raise AssetError(
                f"补丁 blob 越界：[{ref.offset}, {end}) 而 blob 只有 {len(self.blob)} B",
                hint="补丁文件不完整",
            )
        return self.blob[ref.offset : end]

    def _rebuild(
        self,
        target: BundleTarget,
        source: Path,
        sink: Callable[[bytes], None],
    ) -> int:
        """按补丁把**旧文件**重放出来：原头 ＋ 逐块（blob 里的 / **当前文件块表**里的）。

        ⚠ 原样搬的块：字节与长度都从当前文件的块表取（紧凑编码里一个块表都不存）。
        """
        total = 0
        head = self._blob_slice(target.header)
        sink(head)
        total += len(head)
        with ufs.UfsReader(source) as current, source.open("rb") as handle:
            for record in target.blocks:
                if record.blob is not None:
                    data = self._blob_slice(record.blob)
                else:
                    where = record.location
                    if where is None or not 0 <= where < len(current.blocks):
                        raise AssetError(
                            f"块 #{record.index} 的落点 #{where} 在当前文件里不成立",
                            hint="补丁损坏；或当前文件不是写回后的那一份",
                        )
                    length = current.blocks[where].compressed
                    handle.seek(current.block_foff[where])
                    data = handle.read(length)
                    if len(data) != length:
                        raise AssetError(
                            f"块 #{record.index} 只拿到 {len(data):,} 字节，块表声明 {length:,}",
                            hint="当前文件被截断 / 与补丁记的落点对不上",
                        )
                sink(data)
                total += len(data)
        return total

    def rebuild_sha256(self, target: BundleTarget, source: Path | None = None) -> str:
        """**用补丁重放旧文件**，只算 sha256、不落盘 —— 自证与判定性实验用的就是它。"""
        digest = hashlib.sha256()
        self._rebuild(target, Path(source or target.path), digest.update)
        return digest.hexdigest()

    def _restore_bundle(self, target: BundleTarget, *, check_sha: bool) -> dict[str, Any]:
        path = Path(target.path)
        if not path.is_file():
            raise AssetError(f"要还原的文件不在了：{path}", hint="补丁还在，文件却没了")
        current = sha256_of(path)
        if current == target.sha_before:
            return {
                "path": str(path),
                "kind": "bundle",
                "ok": True,
                "skipped": "already",
                "detail": f"{path.name} 就是补丁的装前那份，无需还原",
            }
        if check_sha and current != target.sha_after:
            raise AssetError(
                f"{path.name} 的 sha256 与补丁的「装后」不符：拒绝自动还原",
                hint=(
                    f"补丁声明装后 {target.sha_after[:16]}…，磁盘现在是 {current[:16]}…；"
                    "文件被后来的改动动过 —— 要么人工处理，要么显式丢弃这条补丁"
                ),
            )
        tmp = path.with_name(path.name + ".rmod-restore.tmp")
        tmp.unlink(missing_ok=True)
        digest = hashlib.sha256()
        size = 0
        try:
            with tmp.open("wb") as out:

                def _sink(data: bytes) -> None:
                    out.write(data)
                    digest.update(data)

                size = self._rebuild(target, path, _sink)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if size != target.size_before:
            tmp.unlink(missing_ok=True)
            raise AssetError(
                f"重放出来的旧文件 {size:,} B != 补丁记的 {target.size_before:,} B",
                hint="该文件不还原（原文件一个字节都没动）",
            )
        got = digest.hexdigest()
        if got != target.sha_before:
            tmp.unlink(missing_ok=True)
            raise AssetError(
                f"重放出来的旧文件 sha256 对不上：{got[:16]}… != {target.sha_before[:16]}…",
                hint="该文件不还原（原文件一个字节都没动）",
            )
        _replace_with_retry(tmp, path)
        return {
            "path": str(path),
            "kind": "bundle",
            "ok": True,
            "sha256_before": target.sha_before,
            "sha256_after": target.sha_after,
            "size_before": target.size_before,
            "detail": (
                f"{path.name}：{target.size_after:,} → {target.size_before:,} B；"
                f"sha256 {current[:16]}… → {got[:16]}…（== 装前）"
            ),
        }

    def _restore_ranges(
        self, group: Sequence[RangeTarget], *, check_sha: bool
    ) -> dict[str, Any]:
        """同一个容器上的**几段区间**一趟放回去（选择性写回；⛔ 不拷整包）。

        三把锁（与 _restore_bundle 同一口径）：

        1. 当前 sha256 == 补丁的 sha_before ⇒ **已经还原过**，跳过（幂等，不报错）；
        2. check_sha 时当前 sha256 != sha_after ⇒ **拒绝**（文件被后来的改动动过）；
        3. 重放出来的旧文件 sha256 != sha_before ⇒ **一个字节都不落盘**。

        ⚠ 定位一律按 pathID（⛔ 不用记下来的 stream_start）：前面有对象变长时，
        后面所有对象的起点都会平移。
        """
        head = group[0]
        path = Path(head.path)
        if not path.is_file():
            raise AssetError(f"要还原的文件不在了：{path}", hint="补丁还在，文件却没了")
        for other in group[1:]:
            if other.sha_before != head.sha_before:
                raise AssetError(
                    f"同一个容器上记了几段**基线不同**的区间：{path.name}",
                    hint="补丁损坏（几段必须来自同一次安装前的同一份文件）",
                )
        current = sha256_of(path)
        if current == head.sha_before:
            return {
                "path": str(path),
                "kind": "range",
                "ok": True,
                "skipped": "already",
                "detail": f"{path.name} 就是补丁的装前那份，无需还原",
            }
        if check_sha and current != head.sha_after:
            raise AssetError(
                f"{path.name} 的 sha256 与补丁的「装后」不符：拒绝自动还原",
                hint=(
                    f"补丁声明装后 {head.sha_after[:16]}…，磁盘现在是 {current[:16]}…；"
                    "文件被后来的改动动过 —— 要么人工处理，要么显式丢弃这条补丁"
                ),
            )
        tmp = path.with_name(path.name + ".rmod-restore.tmp")
        tmp.unlink(missing_ok=True)
        try:
            with ufs.UfsReader(path) as reader:
                reps: list[ufs.Replacement] = []
                extra: list[tuple[int, int]] = []
                for target in group:
                    node = reader.node(target.node_path)
                    info = ufs.read_serialized_file(reader, node)
                    row = next(
                        (r for r in info.objects if int(r[1]) == int(target.path_id)), None
                    )
                    if row is None:
                        raise AssetError(
                            f"补丁要还原的 pathID={target.path_id} 不在 {target.node_path} 的"
                            "对象表里",
                            hint="容器被换过 / 对象被删了 —— 这条补丁对不上现场，先人工核对",
                        )
                    stored, size = int(row[2]), int(row[3])
                    if size != target.new_size:
                        raise AssetError(
                            f"pathID={target.path_id} 现在的字节数是 {size:,}，"
                            f"补丁记的是 {target.new_size:,}",
                            hint="这个对象被后来的改动动过 —— 拒绝自动还原（本文件一个字节都没动）",
                        )
                    start = node.offset + info.data_offset + stored
                    extra.append(_run_to_rechunk(reader, target, start, size))
                    reps.append(
                        ufs.Replacement(start, start + size, self._blob_slice(target.old_bytes))
                    )
                ufs.rewrite_container(
                    reader,
                    reps,
                    tmp,
                    # ⚠ 复现装的那一刻的重切边界（BUG-2）：这几段在装的时候被重切过，
                    #   块边界与原始文件不同；不按同一条边界重切，整文件 sha256 回不去。
                    extra_runs=extra,
                )
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        got = sha256_of(tmp)
        if got != head.sha_before:
            tmp.unlink(missing_ok=True)
            raise AssetError(
                f"{path.name} 重放出来的旧文件 sha256 对不上：{got[:16]}… != "
                f"{head.sha_before[:16]}…",
                hint="该文件不还原（原文件一个字节都没动）",
            )
        _replace_with_retry(tmp, path)
        return {
            "path": str(path),
            "kind": "range",
            "ok": True,
            "sha256_before": head.sha_before,
            "sha256_after": head.sha_after,
            "size_before": head.size_before,
            "detail": (
                f"{path.name}：{head.size_after:,} → {head.size_before:,} B；"
                f"sha256 {current[:16]}… → {got[:16]}…（== 装前）；"
                f"把 {len(group)} 段区间放回原位"
            ),
        }

    def _restore_file(self, target: FileTarget, *, check_sha: bool) -> dict[str, Any]:
        path = Path(target.path)
        current = sha256_of(path) if path.is_file() else ""
        if not target.existed:
            if not path.is_file():
                return {
                    "path": str(path),
                    "kind": "file",
                    "ok": True,
                    "skipped": "already",
                    "detail": f"{path.name} 装前就不存在，无需还原",
                }
            if check_sha and current != target.sha_after:
                raise AssetError(
                    f"{path.name} 的 sha256 与补丁的「装后」不符：拒绝删它",
                    hint="这个文件在我们装完之后被动过 —— 不许静默删",
                )
            path.unlink()
            return {
                "path": str(path),
                "kind": "file",
                "ok": True,
                "detail": f"{path.name} 已删除（装前它不存在）",
            }
        if current == target.sha_before:
            return {
                "path": str(path),
                "kind": "file",
                "ok": True,
                "skipped": "already",
                "detail": f"{path.name} 已经是装前那份，无需还原",
            }
        if check_sha and current != target.sha_after:
            raise AssetError(
                f"{path.name} 的 sha256 与补丁的「装后」不符：拒绝自动还原",
                hint="文件被后来的改动动过；要么人工处理，要么显式丢弃这条补丁",
            )
        tmp = path.with_name(path.name + ".rmod-restore.tmp")
        tmp.unlink(missing_ok=True)
        try:
            if target.whole and len(target.parts) == 1 and target.parts[0].offset == 0:
                tmp.write_bytes(self._blob_slice(target.parts[0].blob))
            else:
                with path.open("rb") as src, tmp.open("wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                    for part in target.parts:
                        out.seek(part.offset)
                        out.write(self._blob_slice(part.blob))
                with tmp.open("r+b") as handle:
                    handle.truncate(target.size_before)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        got = sha256_of(tmp)
        size = tmp.stat().st_size
        if size != target.size_before or got != target.sha_before:
            tmp.unlink(missing_ok=True)
            raise AssetError(
                f"{path.name} 重放出来的旧文件自证不过：{size:,} B / sha256 {got[:16]}… ≠ "
                f"{target.size_before:,} B / {target.sha_before[:16]}…",
                hint="该文件不还原（原文件一个字节都没动）",
            )
        _replace_with_retry(tmp, path)
        return {
            "path": str(path),
            "kind": "file",
            "ok": True,
            "sha256_before": target.sha_before,
            "sha256_after": target.sha_after,
            "detail": f"{path.name} 已还原成装前那份（{target.size_before:,} B）",
        }


# --------------------------------------------------------------------------- #
# 补丁柜
# --------------------------------------------------------------------------- #
def _target_matches_disk(target: Target) -> bool:
    """补丁里记的「装后」还等于磁盘现状吗？（合并前用，2026-10-29 加）

    ⛔ 不比就把上一条补丁的陈旧目标并进来 —— 游戏更新换了文件之后，
    那条记录的「装前」是**旧版本**的字节，还原会把游戏写回旧版。
    """
    path = Path(target.path)
    try:
        size = path.stat().st_size
    except OSError:
        return False
    want_size = int(getattr(target, "size_after", 0) or 0)
    if want_size and size != want_size:
        return False
    want_sha = str(getattr(target, "sha_after", "") or "")
    if not want_sha:
        return True
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest() == want_sha


class PatchStore:
    """工程目录下的补丁柜：**一次安装一条**（固定文件名、覆盖式）、还原成功即删。"""

    def __init__(self, root: str | Path, *, game_root: str | Path | None = None) -> None:
        self.root = Path(root)
        self.game_root = None if game_root is None else Path(game_root)
        if self.game_root is not None:
            assert_same_drive(self.root, self.game_root)

    @property
    def json_path(self) -> Path:
        return self.root / PATCH_JSON_NAME

    @property
    def blob_path(self) -> Path:
        return self.root / PATCH_BLOB_NAME

    def pending(self) -> InstallPatch | None:
        """读工程目录里那条还没还原的补丁；没有就 None。"""
        if not self.json_path.is_file():
            return None
        return InstallPatch.load(self.json_path, self.blob_path)

    def has_pending(self) -> bool:
        return self.json_path.is_file()

    def begin(self, label: str, *, merge: bool = True) -> PatchBuilder:
        """开一次安装：把已有补丁并进来（合并），没有就起一条新的。

        ★ 2026-10-29（1.2.2 现场）：合并**只并「装后」与磁盘现状仍然一致**的目标。
        旧的 `begin` 把上一条补丁的目标整条抄进来，而 `_require_merge_base` 只在**重新记同一个路径**
        时才校验 ⇒ 游戏更新换掉 `catalog.bin` 之后，上一条补丁那组「装前/装后」读数
        这两条陈旧记录会原样进新补丁；此时敲 `assets restore` 就会把 1.2.1 的 catalog 字节
        写到 1.2.2 的游戏上。陈旧目标现在被**丢掉**（它们的「装前」属于另一个世界），
        名单记在 `self.dropped_stale` 里供调用方打印。
        """
        pending = self.pending() if merge else None
        if pending is None and self.json_path.is_file():
            # merge=False：显式要求覆盖 —— 老补丁连同它的基线一起丢掉（调用方负责说明后果）。
            self.clear()
        self.dropped_stale: tuple[str, ...] = ()
        if pending is not None:
            keep = [i for i, t in enumerate(pending.targets) if _target_matches_disk(t)]
            if len(keep) != len(pending.targets):
                self.dropped_stale = tuple(
                    str(pending.targets[i].path)
                    for i in range(len(pending.targets))
                    if i not in keep
                )
                pending = pending.keep_only(keep) if keep else None
        return PatchBuilder(self, label=label, pending=pending)

    def clear(self) -> None:
        """删掉补丁（还原成功后 / 用户显式丢弃）。"""
        self.json_path.unlink(missing_ok=True)
        self.blob_path.unlink(missing_ok=True)

    def discard(self) -> tuple[str, ...]:
        """只删补丁、不动文件（用户自认不要这条退路）。"""
        if not self.json_path.is_file():
            return ("（没有未还原的补丁）",)
        patch = self.pending()
        lines = [f"已丢弃补丁：{self.json_path}"] + ([] if patch is None else list(patch.lines()))
        self.clear()
        return tuple(lines)

    def restore(self, *, check_sha: bool = True) -> RestoreReport:
        """还原 + **成功即删补丁**；部分成功 ⇒ **已经回到装前的那几条从柜里剪掉**。

        为什么必须剪（BUG-3，2026-10-11 实机）：units/catalog 还原成功、data.unity3d 被拒
        之后，已还原的目标记录还留在柜里 ⇒ 下一次写被 stale-guard 拦；而唯一的逃生门
        `assets discard` 会把**还没还原**的那几条一起丢掉。
        """
        patch = self.pending()
        if patch is None:
            raise AssetError(
                f"工程目录里没有补丁：{self.json_path}",
                hint="没有反向 Δ 就没有自动还原这条路（⛔ 我们不再拷整包当备份）",
            )
        try:
            report = patch.restore(check_sha=check_sha)
        except BaseException:
            # 半次：先把已经回去的剪掉，再如实抛出（⛔ 不吞）。
            self._prune(patch)
            raise
        if report.ok:
            self.clear()
        else:
            self._prune(patch)
        return report

    def _prune(self, patch: InstallPatch) -> None:
        """把 patch.restored 里那几个目标从柜里去掉；一个不剩 ⇒ 等于还原成功，删补丁。"""
        done = {i for i in patch.restored if 0 <= i < len(patch.targets)}
        if not done:
            return
        keep = [i for i in range(len(patch.targets)) if i not in done]
        if not keep:
            self.clear()
            return
        patch.keep_only(keep).save(self.json_path, self.blob_path)

    def rollback(self) -> tuple[bool, str]:
        """出事时拿这条补丁把文件撤回装前 —— 返回 (成功?, 一句人话)。

        ⛔ 撤销失败**不许吞**：返回 False ＋ 说明，调用方负责把它吼出来
        （补丁留着，用户还能手工再来一次）。写回器与外部库通道**共用这一份实现**。
        """
        try:
            report = self.restore(check_sha=True)
        except BaseException as exc:  # noqa: BLE001 -- 撤销失败必须如实上报，不许吞
            return False, f"⚠ 自动撤销失败（补丁还在 {self.json_path}）：{exc}"
        if not report.ok:
            detail = "；".join(report.lines())
            return False, f"⚠ 自动撤销没全成（补丁还在 {self.json_path}）：{detail}"
        return True, "已用反向 Δ 撤回装前状态（补丁已删）"

    def status_lines(self) -> tuple[str, ...]:
        patch = self.pending()
        if patch is None:
            return (f"[补丁] 无（{self.root}）",)
        return patch.lines()


class PatchBuilder:
    """一次安装的 Δ 记录器：begin → 记基线 → 自证 → commit（或 abort）。"""

    def __init__(self, store: PatchStore, *, label: str, pending: InstallPatch | None) -> None:
        self.store = store
        self.label = label
        self._pending = pending
        self._blob = bytearray(b"" if pending is None else pending.blob)
        self.targets: list[Target] = (
            [] if pending is None else [_copy_target(t) for t in pending.targets]
        )

    # ---------------- blob ----------------
    def _add_blob(self, data: bytes) -> BlobRef:
        ref = BlobRef(len(self._blob), len(data))
        self._blob += data
        return ref

    def _blob_slice(self, ref: BlobRef) -> bytes:
        """读回本 builder 已记的 blob 段。

        ⛔ 2026-10-10 修：`_merge_file_parts` 里调 `self._blob_slice` 时，
        这个方法**只存在于 `InstallPatch`**（读的是已落盘那份），`PatchBuilder` 上没有 ⇒
        "**第二次装同一个整文件**"（把新改动并进已有补丁）直接
        `AttributeError: 'PatchBuilder' object has no attribute '_blob_slice'`。
        触发面很广：补丁柜里已经有一笔时，**再装第二笔就撞上**
        （实测：先改 `RU_T90M 1`、再改 `RU_T72B 1`）。
        """
        end = ref.offset + ref.length
        if ref.offset < 0 or end > len(self._blob):
            raise AssetError(
                f"补丁 blob 越界：[{ref.offset}, {end}) 而 blob 只有 {len(self._blob)} B",
                hint="补丁文件不完整",
            )
        return bytes(self._blob[ref.offset : end])

    # ---------------- 目标查找（合并用） ----------------
    def target_of(self, path: str | Path) -> Target | None:
        want = os.path.normcase(str(Path(path)))
        for target in self.targets:
            if os.path.normcase(str(target.path)) == want:
                return target
        return None

    def _require_merge_base(self, target: Target, current_sha: str) -> None:
        if target.sha_after and target.sha_after != current_sha:
            raise AssetError(
                f"已有补丁的「装后」与磁盘现状对不上：{target.path}",
                hint=(
                    "合并会把别人的改动当成基线 ⇒ 拒绝。先 `assets status` 核对那一条"
                    "（看「装后」与盘上各是什么），再二选一："
                    "① `assets restore` 还原；② 确认这条补丁作废后 `assets discard` "
                    "（⛔ discard 会丢掉退路）。⚠ 柜是按 `--project-root` 分的 —— "
                    "不同装机入口用了不同 project-root 时，先把上一次那条柜找对。"
                ),
            )

    # ---------------- 两种基线 ----------------
    def capture_bundle(
        self,
        reader: ufs.UfsReader,
        *,
        placement: Mapping[int, int | None],
        sha_before: str,
        size_before: int,
    ) -> BundleTarget:
        """记下**旧**容器的反向 Δ。

        placement：旧块下标 → 新块下标（**原样搬**的那些）；不在表里的 = 这次要被重压/拆开，
        ⇒ 它的原始压缩字节必须存进补丁。

        落点语义：location = 该块的字节**写完之后**在新文件的第几块（紧凑编码按
        「连续块 + 同一个平移量」压成 verbatim_runs；还原时按当前文件块表取字节）。
        """
        path = Path(reader.path)
        data_start = reader.block_foff[0] if reader.blocks else 0
        existing = self.target_of(path)
        if isinstance(existing, BundleTarget):
            self._require_merge_base(existing, sha_before)
            return self._rebase_bundle(reader, existing, placement)
        if existing is not None:
            raise AssetError(
                f"{path.name} 在补丁里记的是另一种目标（{existing.kind}）",
                hint="补丁与磁盘状态不一致，先还原或丢弃",
            )
        starts = reader.block_foff
        with path.open("rb") as handle:
            header = self._add_blob(handle.read(data_start))
            target = BundleTarget(
                path=path,
                sha_before=sha_before,
                size_before=size_before,
                data_start=data_start,
                header=header,
            )
            for index, block in enumerate(reader.blocks):
                new_index = placement.get(index)
                if new_index is None:
                    handle.seek(starts[index])
                    blob = self._add_blob(handle.read(block.compressed))
                    target.blocks.append(
                        BlockRecord(index=index, compressed=block.compressed, blob=blob)
                    )
                else:
                    target.blocks.append(
                        BlockRecord(index=index, compressed=block.compressed, location=new_index)
                    )
        self.targets.append(target)
        return target

    def _rebase_bundle(
        self,
        reader: ufs.UfsReader,
        target: BundleTarget,
        placement: Mapping[int, int | None],
    ) -> BundleTarget:
        """第二次安装：已存字节的块不动；还指在当前文件里的块搬到新落点、或就地存下来。"""
        starts = reader.block_foff
        with Path(target.path).open("rb") as handle:
            for record in target.blocks:
                if record.blob is not None:
                    continue
                where = record.location
                if where is None or where >= len(starts):
                    raise AssetError(
                        f"{target.path.name} 的补丁块 #{record.index} 落点 #{where} 不成立",
                        hint="补丁损坏",
                    )
                length = reader.blocks[where].compressed
                handle.seek(starts[where])
                data = handle.read(length)
                new_index = placement.get(where)
                if new_index is None:
                    record.blob = self._add_blob(data)
                    record.compressed = length
                    record.location = None
                else:
                    record.location = new_index
                    record.compressed = length
        return target

    def capture_file(
        self,
        path: str | Path,
        *,
        before: bytes | None,
        after: bytes,
    ) -> FileTarget:
        """记一个**整文件**目标。before=None = 装前它不存在（还原 = 删掉它）。

        同长 ⇒ 只存**差异块**（4 KB 对齐；实测 catalog 改一条 m_Crc 只差 10 B）；
        长度变了 ⇒ 回退成整份（parts 覆盖 [0, size_before)，还原时不用拷当前文件）。
        """
        target_path = Path(path)
        existing = self.target_of(target_path)
        if isinstance(existing, FileTarget):
            self._require_merge_base(existing, "" if before is None else _sha256_bytes(before))
            if before is not None:
                self._merge_file_parts(existing, before, after)
            existing.sha_after = _sha256_bytes(after)
            existing.size_after = len(after)
            return existing
        if existing is not None:
            raise AssetError(
                f"{target_path.name} 在补丁里记的是另一种目标（{existing.kind}）",
                hint="补丁与磁盘状态不一致，先还原或丢弃",
            )
        target = FileTarget(
            path=target_path,
            existed=before is not None,
            sha_before="" if before is None else _sha256_bytes(before),
            size_before=0 if before is None else len(before),
        )
        if before is not None:
            self._fill_file_parts(target, before, after)
        target.sha_after = _sha256_bytes(after)
        target.size_after = len(after)
        self.targets.append(target)
        return target

    def range_of(
        self, path: str | Path, *, node_path: str | None = None, path_id: int | None = None
    ) -> RangeTarget | None:
        """补丁里有没有同一段区间的记录（合并用）。"""
        want = os.path.normcase(str(Path(path)))
        for target in self.targets:
            if not isinstance(target, RangeTarget):
                continue
            if os.path.normcase(str(target.path)) != want:
                continue
            if node_path is not None and target.node_path != str(node_path):
                continue
            if path_id is not None and target.path_id != int(path_id):
                continue
            return target
        return None

    def capture_range(
        self,
        *,
        path: str | Path,
        node_path: str,
        path_id: int,
        stream_start: int,
        old_bytes: bytes,
        new_size: int,
        label: str = "",
        sha_before: str = "",
        sha_after: str = "",
        size_before: int = 0,
        size_after: int = 0,
        run_start: int = 0,
        run_end: int = 0,
    ) -> RangeTarget:
        """记一段**解压流区间**的反向 Δ（⛔ 不拷整包；只存被替换那一段的原字节）。

        run_start / run_end = **装的那一刻会被重切的那一段**（装后坐标），由
        `ufs.rewrite_run()` 在写回前算出来。还原时按它复现同一条重切边界 ——
        少了这一对，长度一变，装后文件的块边界就与原始文件不同，整文件 sha256 回不去。

        同一个 (容器, 节点, pathID) 第二次记 ⇒ **基线保持最早那份**：old_bytes /
        old_size / sha_before / size_before 一律不动，只把 new_size / sha_after 跟到最新
        —— 那才是「还原回用户装之前那一刻」。基线对不上（当前 sha ≠ 补丁的 sha_after）
        ⇒ 拒绝合并（防拿别人的改动当基线）。
        """
        target_path = Path(path)
        existing = self.range_of(target_path, node_path=node_path, path_id=path_id)
        if existing is not None:
            self._require_merge_base(existing, sha_before)
            # 重切段的**并集**：旧记录那一段尾巴在本次写入点之后 ⇒ 跟着平移 delta。
            # 不并的话，第一次安装留下的块边界在第二次安装之后就没人负责还原了。
            shift = int(new_size) - existing.new_size
            if run_end > run_start:
                if existing.run_end > existing.run_start:
                    existing.run_start = min(existing.run_start, run_start)
                    existing.run_end = max(existing.run_end + shift, run_end)
                else:
                    existing.run_start, existing.run_end = int(run_start), int(run_end)
            existing.new_size = int(new_size)
            existing.sha_after = sha_after
            existing.size_after = int(size_after)
            if label:
                existing.label = label
            return existing
        ref = self._add_blob(bytes(old_bytes))
        target = RangeTarget(
            path=target_path,
            node_path=str(node_path),
            path_id=int(path_id),
            stream_start=int(stream_start),
            old_size=len(old_bytes),
            new_size=int(new_size),
            old_bytes=ref,
            label=label,
            sha_before=sha_before,
            sha_after=sha_after,
            size_before=int(size_before),
            size_after=int(size_after),
            run_start=int(run_start),
            run_end=int(run_end),
        )
        self.targets.append(target)
        return target

    def self_check_range(
        self, target: RangeTarget, *, live_path: str | Path | None = None
    ) -> str:
        """range 记录的自证（读盘；⛔ 不通过就不许 commit）。

        三条：① **当下那串新字节**（live_path，默认 = target.path）在盘上、且它的
        sha256 == 记录里那个 sha_after；② 补丁里那段的长度 == old_size；
        ③ 按 pathID 重新定位到的对象长度 == new_size（＝ 还原时定位得到、且对得上）。

        ⚠ live_path 与 target.path **分开**是给 --in-place 那条链用的：那一刻新字节还在
        同目录的**临时文件**里（东西尚未改名就位），而补丁要记的落点是**容器本身**。
        """
        where = Path(live_path) if live_path is not None else Path(target.path)
        if not where.is_file():
            raise AssetError(f"range 记录的产物不在盘上：{where}", hint="先写产物，再记补丁")
        got = sha256_of(where)
        if got != target.sha_after:
            raise AssetError(
                f"{where.name} 的 sha256 与 range 记录的「装后」不符：{got[:16]}… != "
                f"{target.sha_after[:16]}…",
                hint="补丁记的不是这一份产物；⛔ 不许 commit",
            )
        blob = self._blob_slice(target.old_bytes)
        if len(blob) != target.old_size:
            raise AssetError(
                f"补丁里那段原始字节 {len(blob):,} B != 记录的 {target.old_size:,} B",
                hint="补丁不完整；⛔ 不许 commit",
            )
        with ufs.UfsReader(where) as reader:
            node = reader.node(target.node_path)
            info = ufs.read_serialized_file(reader, node)
            row = next(
                (r for r in info.objects if int(r[1]) == int(target.path_id)), None
            )
        if row is None:
            raise AssetError(
                f"产物里没有 pathID={target.path_id}（节点 {target.node_path}）",
                hint="记错了对象 / 产物不是这一份；⛔ 不许 commit",
            )
        if int(row[3]) != target.new_size:
            raise AssetError(
                f"产物里 pathID={target.path_id} 是 {int(row[3]):,} B，"
                f"记录的改后长度是 {target.new_size:,} B",
                hint="补丁与产物对不上；⛔ 不许 commit",
            )
        return got

    def _fill_file_parts(self, target: FileTarget, before: bytes, after: bytes) -> None:
        """基线 = 旧文件里**与写出去那份不同**的段。"""
        if len(before) != len(after):
            target.parts = [FilePart(0, self._add_blob(before))]
            target.whole = True
            return
        for start in range(0, len(before), _FILE_CHUNK):
            end = min(start + _FILE_CHUNK, len(before))
            if before[start:end] != after[start:end]:
                target.parts.append(FilePart(start, self._add_blob(before[start:end])))

    def _merge_file_parts(self, target: FileTarget, before: bytes, after: bytes) -> None:
        """第二次装同一个整文件：**基线保持最早那份**（A），只补这次新差出来的段。"""
        if target.whole:
            return
        if len(before) != len(after):
            # 长度会变 ⇒ 回退整份：A = 把已记的段盖回当前文件
            whole = _apply_parts(before, target.parts, self._blob_slice)
            target.parts = [FilePart(0, self._add_blob(whole))]
            target.whole = True
            return
        covered = [(part.offset, part.offset + part.blob.length) for part in target.parts]
        for start in range(0, len(before), _FILE_CHUNK):
            end = min(start + _FILE_CHUNK, len(before))
            if before[start:end] == after[start:end]:
                continue
            if any(lo <= start and end <= hi for lo, hi in covered):
                continue
            target.parts.append(FilePart(start, self._add_blob(before[start:end])))

    # ---------------- 自证 ----------------
    def self_check(self, target: BundleTarget, *, strict: bool = False) -> str:
        """Δ 记录的自证。

        默认是**结构自证**（不读盘）：原头 ＋ 逐块的压缩长度必须**首尾相接铺满**整个旧文件
        —— UnityFS 的块本来就从 data_start 起连续排布，所以「铺满」= 「重放必然等于旧文件」。
        strict=True 再把旧文件真读一遍重放并比 sha256（判定性实验 / 逐位复核用）。
        """
        if target.header.length != target.data_start:
            raise AssetError(
                f"原头 {target.header.length:,} B != data_start {target.data_start:,}",
                hint="Δ 记录不完整；⛔ 不许拿这种补丁去写文件",
            )
        total = target.data_start
        for expect, record in enumerate(target.blocks):
            if record.index != expect:
                raise AssetError(
                    f"补丁块序号断了：第 {expect} 位是 #{record.index}",
                    hint="Δ 记录不完整；⛔ 不许拿这种补丁去写文件",
                )
            if record.blob is not None:
                if record.blob.length != record.compressed:
                    raise AssetError(
                        f"块 #{record.index}：存下来 {record.blob.length:,} B，"
                        f"块表声明 {record.compressed:,} B",
                        hint="Δ 记录不一致；⛔ 不许拿这种补丁去写文件",
                    )
            elif record.location is None:
                raise AssetError(
                    f"块 #{record.index} 既没存字节、也没有落点",
                    hint="Δ 记录不完整；⛔ 不许拿这种补丁去写文件",
                )
            total += record.compressed
        if total != target.size_before:
            raise AssetError(
                f"Δ 铺出来的旧文件 {total:,} B != 实际 {target.size_before:,} B",
                hint="Δ 记录不完整；⛔ 不许拿这种补丁去写文件",
            )
        if strict:
            got = self._as_patch().rebuild_sha256(target)
            if got != target.sha_before:
                raise AssetError(
                    f"反向 Δ 自证不过：重放出来 {got[:16]}… != 装前 {target.sha_before[:16]}…",
                    hint="Δ 记录不完整；⛔ 不许拿这种补丁去写文件",
                )
            return got
        return target.sha_before

    def set_after(self, target: Target, *, sha_after: str, size_after: int) -> None:
        target.sha_after = sha_after
        target.size_after = size_after

    # ---------------- 落盘 ----------------
    def _as_patch(self) -> InstallPatch:
        has_range = any(isinstance(t, RangeTarget) for t in self.targets)
        return InstallPatch(
            blob=bytes(self._blob),
            targets=self.targets,
            label=self.label,
            created=time.strftime("%Y-%m-%d %H:%M:%S"),
            game_root="" if self.store.game_root is None else str(self.store.game_root),
            # ⚠ 含 range 才写 3：老版本产品读到 3 会**明确拒绝**；只有 bundle/file 的
            #    补丁继续写 2 ⇒ 既有的 v2 补丁一条都不作废。
            format=RANGE_FORMAT_VERSION if has_range else FORMAT_VERSION,
        )

    def commit(self) -> Path:
        """把合并后的补丁写进工程目录（**原子**：先 blob 后 json；覆盖旧的那条）。"""
        patch = self._as_patch()
        for target in patch.targets:
            if not target.sha_after:
                raise AssetError(
                    f"{target.path.name} 没记「装后」sha256，补丁不完整",
                    hint="先 set_after 再 commit",
                )
        self.store.root.mkdir(parents=True, exist_ok=True)
        blob_tmp = self.store.blob_path.with_name(self.store.blob_path.name + ".tmp")
        json_tmp = self.store.json_path.with_name(self.store.json_path.name + ".tmp")
        blob_tmp.write_bytes(patch.blob)
        json_tmp.write_text(
            json.dumps(patch.to_json(), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        _replace_with_retry(blob_tmp, self.store.blob_path)
        _replace_with_retry(json_tmp, self.store.json_path)
        return self.store.json_path

    def abort(self) -> None:
        """放弃这次记录（只丢内存里的，不动盘上已有的补丁）。"""
        self.targets = (
            [] if self._pending is None else [_copy_target(t) for t in self._pending.targets]
        )
        self._blob = bytearray(b"" if self._pending is None else self._pending.blob)


# --------------------------------------------------------------------------- #
# 写回器要用的一小块：旧块 → 新块的「原样搬」落位表
# --------------------------------------------------------------------------- #
def verbatim_placement(
    block_count: int,
    *,
    dirty: Sequence[int] | Mapping[int, Any],
    verbatim_from: Mapping[int, int] | None,
) -> dict[int, int]:
    """算 placement：旧块 → 新块（**压缩字节原样搬**的那些）。

    * L1（verbatim_from is None）：新块与旧块一一对应 ⇒ 除 dirty 之外全部原样搬；
    * Δ：verbatim_from 是 {新块: 旧块} ⇒ 反查；没出现的旧块是被重压/拆开了。
    """
    if verbatim_from is None:
        return {i: i for i in range(block_count) if i not in dirty}
    return {int(old): int(new) for new, old in verbatim_from.items()}
