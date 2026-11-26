r"""VT 池子的**装 / 卸 / 查**：把烘出来的 5 个文件（`<group>.gts` ＋ `<group>0_1..4.gtp`）
装进 `<游戏>\BrokenArrow_Data\StreamingAssets` 这个目录。

## 为什么与 `vt_stack` 分成两个模块

* `vt_stack`：**修改**材质里那两个字段（`m_BuildTextureStacks`，等长写回），撤销靠反向 Δ（补丁柜）；
* `vt_pool`（本模块）：**新增**池子文件（`.gts` / `.gtp`），撤销 = **删掉即回退**（AGENTS §0.10）。

共用的只有「就位」那一个口径：`vt_stack.streaming_dir_of` / `vt_stack.pool_status`
（盘上有没有、大小对不对）。本模块在它之上补 **sha256** —— 判据要的是**内容逐字节相同**，
不是「名字相同」，更不是「存在」。

## 账本（清单）住在哪儿

    <游戏>\BrokenArrow_Data\StreamingAssets\ba-mod-maker-vt-pool.json

与那 5 个文件**同目录**，两条理由：① `remove` / `status` 只拿得到 `--game`
（既没有 `--pool` 也没有 `--project-root`），账本必须能从游戏根推出来；
② 「卸干净」= 我们加进这一目录的东西**一个不剩**（含账本自己）。
一条记三样：`name` / `size` / `sha256`（形状照研究件
`_work/paint-design/vt-install-manifest.json`，⛔ 不照抄它的硬编码路径）。

## 三条硬口径

1. **默认干跑**：不打 `--apply` 时**一个字节都不写** —— 连账本也不写、连目录都不建。
   装 / 卸的真值都是**算出来的**（`sha256_of` 读的是真文件）。
2. **只删账本里记着、且 sha256 相符的那些**：盘上那个文件与账本记的 sha 不同 ⇒ **拒绝删**，
   并印出「哪个文件 · 期望 sha256 · 实际 sha256」。这正是研究件
   `_work/paint-design/vt_uninstall.py` 缺的那一条：它印了 `sha 一致=False` 却照删。
3. **不许静默覆盖**：盘上已有同名文件时，sha 相同 ⇒ 报「已就位」**一个字节都不改**；
   sha 不同 ⇒ **拒绝**（要显式 `force=True`）。

## 判据一行

    vt pool install --pool <池子> --game <游戏根> --apply
    -> StreamingAssets 里 `.gts/.gtp` **0 → 5**，逐个 sha256 == 源，账本写下 5 条；
    vt pool remove --game <游戏根> --apply
    -> **5 → 0**（含账本）；把其中一个文件改 1 个字节再 remove ⇒ **必须红**
    （那个文件仍在、退出码 1，消息里有它的两个 sha256）。
"""

from __future__ import annotations

import json
import shutil
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import vt_stack
from ba_mod_maker.asset_kernel.reverse_delta import sha256_of
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "DESCRIPTOR_BYTES",
    "FREE_RECT_PAGE_CAP",
    "GTS_HEADER_BYTES",
    "GTS_MAGIC",
    "GTS_SENTINEL",
    "GTS_VERSION",
    "FreeRect",
    "GtsLevel",
    "GtsTable",
    "MANIFEST_FORMAT",
    "MANIFEST_NAME",
    "ManifestEntry",
    "PageFileDescriptor",
    "PoolEntry",
    "ShardFile",
    "PoolInstallReport",
    "PoolManifest",
    "PoolPlan",
    "PoolRemovePlan",
    "PoolRemoveReport",
    "PoolStats",
    "PoolStatus",
    "SentinelReading",
    "gtp_shards",
    "gts_sentinel",
    "install",
    "level0_free_rects",
    "load_manifest",
    "manifest_path_of",
    "plan_install",
    "plan_remove",
    "pool_stats",
    "pool_tables",
    "read_gts",
    "remove",
    "status",
]

#: 账本（清单）的文件名：与池子文件**同目录**（见模块头：只拿得到 `--game`）。
MANIFEST_NAME = "ba-mod-maker-vt-pool.json"

#: 账本格式版本。改了字段含义就 +1（旧账本会被**拒绝**而不是猜着读，与反向 Δ 同一个口径）。
MANIFEST_FORMAT = 1

#: 账本里那三个字段之外的东西（写进去只为**人读**，⛔ 不参与任何判据）。
MANIFEST_TOOL = "ba-mod-maker vt pool install"


# --------------------------------------------------------------------------- #
# 一个文件：源那一侧 ＋ 盘上那一侧
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class PoolEntry:
    """池子里的一个文件：**源那一侧**三样（名字 / 字节数 / sha256）＋**盘上那一侧**的现状。

    `sha256` 才是判据：名字对、大小对都不算数 —— 研究件 `vt_uninstall.py` 就是
    印了 `sha 一致=False` 却照删（第 2 条硬口径治的就是它）。
    """

    name: str
    size: int
    sha256: str
    dest: Path
    installed_size: int | None = None
    installed_sha256: str | None = None

    @property
    def ok(self) -> bool:
        """盘上那个文件**逐字节**就是这一份。"""
        return self.installed_sha256 is not None and self.installed_sha256 == self.sha256

    @property
    def absent(self) -> bool:
        """盘上还没有它。"""
        return self.installed_size is None

    @property
    def state(self) -> str:
        """盘上现状。措辞与 `vt_stack.PoolFile.state` 对齐（多一档 `sha 不符`）。"""
        if self.installed_size is None:
            return "缺"
        if self.installed_size != self.size:
            return f"大小不符（盘上 {self.installed_size:,} B）"
        if self.installed_sha256 != self.sha256:
            return "sha 不符"
        return "就位"

    def shas(self) -> str:
        """两个 sha256 摆在一行 —— 拒绝覆盖 / 拒绝删的时候**必须**印出来。"""
        return f"期望 {self.sha256} · 实际 {self.installed_sha256 or '（不在）'}"


# --------------------------------------------------------------------------- #
# 装：干跑计划 -> 真装
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class PoolPlan:
    """一次 `install` 的干跑计划（⛔ 一个字节都没写）。"""

    game_root: Path
    streaming_dir: Path
    pool_dir: Path
    manifest_path: Path
    entries: tuple[PoolEntry, ...]
    #: 账本里**已经**记着的名字（只读一眼；装的时候按名字合并，别把以前装的丢了）。
    manifest_records: tuple[str, ...] = ()

    @property
    def ready(self) -> tuple[PoolEntry, ...]:
        """盘上已经逐字节相同 ⇒ **不改**（P4 的「已就位」）。"""
        return tuple(entry for entry in self.entries if entry.ok)

    @property
    def new(self) -> tuple[PoolEntry, ...]:
        """盘上还没有 ⇒ 新增。"""
        return tuple(entry for entry in self.entries if entry.absent)

    @property
    def conflicts(self) -> tuple[PoolEntry, ...]:
        """盘上有同名文件、但**不是这一份** ⇒ 默认拒绝覆盖（要 `force=True`）。"""
        return tuple(entry for entry in self.entries if not entry.ok and not entry.absent)

    def criterion_lines(self) -> tuple[str, ...]:
        """干跑那几行（判据一行）。CLI 与用例共用同一份措辞。"""
        many = len(self.manifest_records)
        records = f"（已经记着 {many} 个）" if many else "（还不在）"
        lines = [
            f"[VT 池子 · 装] {self.pool_dir}",
            f"  目标目录 : {self.streaming_dir}",
            f"  账本     : {self.manifest_path}{records}",
            f"  计划     : 新增 {len(self.new)} 个 · 已就位（不改）{len(self.ready)} 个"
            f" · 拒绝覆盖 {len(self.conflicts)} 个",
        ]
        for entry in self.entries:
            if entry.ok:
                lines.append(f"  ✅ {entry.name}  {entry.size:,} B  已就位（一个字节都不改）")
            elif entry.absent:
                lines.append(f"  +  {entry.name}  {entry.size:,} B  新增  sha256 {entry.sha256}")
            else:
                lines.append(f"  ⛔ {entry.name}  拒绝覆盖：{entry.state} · {entry.shas()}")
        return tuple(lines)


@dataclass(frozen=True, slots=True)
class PoolInstallReport:
    """一次真装的结果（`added` 里每一个都过了**写后复核**）。"""

    plan: PoolPlan
    added: tuple[str, ...]
    kept: tuple[str, ...]
    manifest_written: bool

    def report_lines(self) -> tuple[str, ...]:
        lines = [f"  新增 {len(self.added)} 个 · 已就位（不改）{len(self.kept)} 个"]
        lines += [f"  + {name}" for name in self.added]
        lines += [f"  = {name}（已就位，一个字节都没改）" for name in self.kept]
        lines.append(f"  写后复核：{len(self.added)}/{len(self.added)} 个 sha256 == 源")
        lines.append(
            f"  账本 {self.plan.manifest_path} —— "
            + ("已更新" if self.manifest_written else "内容没变，没写")
        )
        return tuple(lines)


def plan_install(pool_dir: str | Path, game_root: str | Path) -> PoolPlan:
    """干跑：源那 5 个文件 vs 盘上现状（⛔ 一个字节都不写）。

    「盘上有没有、多大」走 `vt_stack.pool_status`（产品里**唯一**那个口径）；
    「是不是同一份」由本模块补 **sha256** —— 判据是内容相同，不是名字相同、也不是大小相同。
    """
    source = Path(pool_dir)
    streaming = vt_stack.streaming_dir_of(game_root)
    rows = vt_stack.pool_status(source, streaming)
    entries: list[PoolEntry] = []
    for row in rows:
        installed = streaming / row.name
        entries.append(
            PoolEntry(
                name=row.name,
                size=row.size,
                sha256=sha256_of(source / row.name),
                dest=installed,
                installed_size=row.installed_size,
                installed_sha256=(sha256_of(installed) if row.installed_size is not None else None),
            )
        )
    manifest_path = manifest_path_of(game_root)
    manifest = load_manifest(manifest_path)
    return PoolPlan(
        game_root=Path(game_root),
        streaming_dir=streaming,
        pool_dir=source,
        manifest_path=manifest_path,
        entries=tuple(entries),
        manifest_records=tuple(record.name for record in manifest.entries) if manifest else (),
    )


def install(plan: PoolPlan, *, force: bool = False) -> PoolInstallReport:
    """真装：补上缺的那些 ＋ 写账本。

    ⛔ 默认**拒绝覆盖**盘上 sha 不同的同名文件（那是别人的东西）；`force=True` 才盖。
    已经就位的一个字节都不改；写完的每一个都过**写后复核**（sha256 读回来对源）。
    """
    conflicts = plan.conflicts
    if conflicts and not force:
        names = "、".join(entry.name for entry in conflicts)
        raise AssetError(
            f"盘上已有同名文件、且不是这一份（sha256 不同）：{len(conflicts)} 个 —— 拒绝覆盖",
            hint=f"{names}：先看清两边的 sha256（干跑报告里逐条印了）；"
            "确实要盖掉它，才在命令上加 --force",
        )
    plan.streaming_dir.mkdir(parents=True, exist_ok=True)
    records: dict[str, ManifestEntry] = {r.name: r for r in _records_of(plan.manifest_path)}
    added: list[str] = []
    kept: list[str] = []
    try:
        for entry in plan.entries:
            if entry.ok:
                kept.append(entry.name)
            else:
                _copy_verified(plan.pool_dir / entry.name, entry)
                added.append(entry.name)
            records[entry.name] = ManifestEntry(entry.name, entry.size, entry.sha256)
    except BaseException:
        if added:
            # 已经落盘的那几个**必须**留在账上 —— 否则它们变成「删不掉的无主文件」。
            _write_manifest(plan, _ordered(plan, records))
        raise
    written = _write_manifest(plan, _ordered(plan, records))
    return PoolInstallReport(
        plan=plan, added=tuple(added), kept=tuple(kept), manifest_written=written
    )


# --------------------------------------------------------------------------- #
# 卸：只删账本里记着、且 sha256 相符的那些
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class PoolRemovePlan:
    """一次 `remove` 的干跑计划（⛔ 一个字节都没删）。"""

    game_root: Path
    streaming_dir: Path
    manifest_path: Path
    manifest: PoolManifest | None
    entries: tuple[PoolEntry, ...]

    @property
    def deletable(self) -> tuple[PoolEntry, ...]:
        """账本里记着、**且盘上 sha256 相符**的那些 —— 只有这些可以删。"""
        return tuple(entry for entry in self.entries if entry.ok)

    @property
    def refused(self) -> tuple[PoolEntry, ...]:
        """盘上还在、但**不是我们装的那一份**（被改过 / 被换过）⇒ 拒绝删。"""
        return tuple(entry for entry in self.entries if not entry.ok and not entry.absent)

    @property
    def gone(self) -> tuple[PoolEntry, ...]:
        """账本记着，但盘上已经没有了。"""
        return tuple(entry for entry in self.entries if entry.absent)

    def criterion_lines(self) -> tuple[str, ...]:
        lines = [f"[VT 池子 · 卸] {self.manifest_path}"]
        lines.append(f"  目标目录 : {self.streaming_dir}")
        if self.manifest is None:
            lines.append("  账本不在 ⇒ 这一版没往游戏里装过池子：没有可删的")
            return tuple(lines)
        lines.append(
            f"  计划     : 可删 {len(self.deletable)} 个 · 拒绝删 {len(self.refused)} 个"
            f" · 已不在 {len(self.gone)} 个"
        )
        for entry in self.entries:
            if entry.absent:
                lines.append(f"  ·  {entry.name}（已不在）")
            elif entry.ok:
                lines.append(f"  −  {entry.name}  {entry.size:,} B  可删（sha256 相符）")
            else:
                lines.append(f"  ⛔ {entry.name}  拒绝删：{entry.state} · {entry.shas()}")
        if self.refused:
            lines.append("  ⚠ 被改过的**一个都不删**（只删自己加进去的那一份）")
        return tuple(lines)


@dataclass(frozen=True, slots=True)
class PoolRemoveReport:
    """一次真卸的结果。`refused` 非空 = 有红（退出码 1），且账本**留着**（可重跑）。"""

    plan: PoolRemovePlan
    deleted: tuple[str, ...]
    refused: tuple[str, ...]
    manifest_removed: bool

    def report_lines(self) -> tuple[str, ...]:
        lines: list[str] = []
        for entry in self.plan.entries:
            if entry.name in self.deleted:
                lines.append(f"  − {entry.name}  {entry.size:,} B  已删（sha256 相符）")
            elif entry.name in self.refused:
                lines.append(f"  ⛔ {entry.name}  拒绝删：{entry.state} · {entry.shas()}")
            else:
                lines.append(f"  · {entry.name}（已不在）")
        lines.append(
            f"  账本 {self.plan.manifest_path} —— "
            + ("已删" if self.manifest_removed else "留着（还有没删掉的，可以重跑）")
        )
        return tuple(lines)


def plan_remove(game_root: str | Path) -> PoolRemovePlan:
    """干跑：账本里记着的每一个文件，盘上现在是什么样（⛔ 一个字节都没删）。"""
    streaming = vt_stack.streaming_dir_of(game_root)
    manifest_path = manifest_path_of(game_root)
    manifest = load_manifest(manifest_path)
    entries: tuple[PoolEntry, ...] = ()
    if manifest is not None:
        entries = tuple(
            _entry_from_record(record, streaming / record.name) for record in manifest.entries
        )
    return PoolRemovePlan(
        game_root=Path(game_root),
        streaming_dir=streaming,
        manifest_path=manifest_path,
        manifest=manifest,
        entries=entries,
    )


def remove(plan: PoolRemovePlan) -> PoolRemoveReport:
    """真卸：删掉**sha256 相符**的那些；被改过的**一个都不删**（并报红）。"""
    deleted: list[str] = []
    refused: list[str] = []
    for entry in plan.entries:
        if entry.absent:
            continue
        if not entry.ok:
            refused.append(entry.name)
            continue
        try:
            entry.dest.unlink()
        except OSError as exc:
            raise AssetError(
                f"删不掉：{entry.dest}（{exc}）",
                hint="游戏在跑 / 文件被别的程序占着都会这样；关掉游戏再试（半个都没删）",
            ) from exc
        deleted.append(entry.name)
    manifest_removed = False
    if not refused and plan.manifest is not None:
        # 账本里记的东西（我们加进游戏目录的）一个不剩 ⇒ 账本自己也撤掉。
        plan.manifest_path.unlink()
        manifest_removed = True
    return PoolRemoveReport(
        plan=plan,
        deleted=tuple(deleted),
        refused=tuple(refused),
        manifest_removed=manifest_removed,
    )


# --------------------------------------------------------------------------- #
# 查：账本 × 盘上（＋可选：源目录 × 盘上，那一读走 vt_stack.pool_status）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class PoolStatus:
    """`status` 的读数（**只读**）。"""

    plan: PoolRemovePlan
    source_dir: Path | None = None
    source: tuple[vt_stack.PoolFile, ...] = ()

    @property
    def streaming_dir(self) -> Path:
        return self.plan.streaming_dir

    @property
    def manifest_path(self) -> Path:
        return self.plan.manifest_path

    @property
    def manifest(self) -> PoolManifest | None:
        return self.plan.manifest

    @property
    def entries(self) -> tuple[PoolEntry, ...]:
        return self.plan.entries

    @property
    def missing(self) -> tuple[PoolEntry, ...]:
        return tuple(entry for entry in self.entries if not entry.ok)

    @property
    def ok(self) -> bool:
        """账本在、且记着的每一个都**逐字节**就位。"""
        return self.manifest is not None and bool(self.entries) and not self.missing

    def criterion_lines(self) -> tuple[str, ...]:
        known = self.manifest is not None
        records = f"（记着 {len(self.manifest.entries)} 个）" if known else "（不在：没装过）"
        lines = [
            f"[VT 池子 · 查] {self.streaming_dir}",
            f"  账本     : {self.manifest_path}{records}",
        ]
        for entry in self.entries:
            mark = "✅" if entry.ok else "❌"
            lines.append(f"  {mark} {entry.name}  {entry.size:,} B  {entry.state}")
        if not self.entries:
            lines.append("  （账本里一个文件都没有）")
        if not known:
            lines.append("  判据     : 没装过（账本不在）—— 该装：vt pool install --apply")
        else:
            done = len(self.entries) - len(self.missing)
            lines.append(f"  判据     : {done}/{len(self.entries)} 就位")
        if self.source_dir is not None:
            lines.append(f"  源对照   : {self.source_dir}（只读，不走账本）")
            for row in self.source:
                mark = "✅" if row.ok else "❌"
                lines.append(f"  {mark} {row.name}  {row.size:,} B  {row.state}")
        return tuple(lines)


def status(game_root: str | Path, *, pool_dir: str | Path | None = None) -> PoolStatus:
    """只读：账本记着的池子文件在盘上就位没有（给了 `pool_dir` 再把那 5 个对一遍）。

    `pool_dir` 那一读走 `vt_stack.pool_status`（与 `vt … --pool` 同一口径），
    ⛔ 它**不**参与「就位」的结论 —— 结论只认账本 × sha256。
    """
    plan = plan_remove(game_root)
    source_dir: Path | None = None
    source: tuple[vt_stack.PoolFile, ...] = ()
    if pool_dir is not None:
        source_dir = Path(pool_dir)
        source = vt_stack.pool_status(source_dir, plan.streaming_dir)
    return PoolStatus(plan=plan, source_dir=source_dir, source=source)


# --------------------------------------------------------------------------- #
# 账本：读写 ＋ 字段自证
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """账本里的一条：`name` / `size` / `sha256`（研究件那份就是这三个字段的口径）。"""

    name: str
    size: int
    sha256: str

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "size": self.size, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class PoolManifest:
    """盘上那份账本。`pool_dir` 只为**人读**（⛔ 不参与判据：判据是逐条的 sha256）。"""

    path: Path
    pool_dir: str
    entries: tuple[ManifestEntry, ...]


def manifest_path_of(game_root: str | Path) -> Path:
    r"""账本落点：与池子文件**同目录**（`<游戏>\BrokenArrow_Data\StreamingAssets`）。"""
    return vt_stack.streaming_dir_of(game_root) / MANIFEST_NAME


def load_manifest(path: str | Path) -> PoolManifest | None:
    """读账本；**不在**时返回 `None`（不是错误）。读不开 / 版本不对 ⇒ 抛（⛔ 不许当空账本）。"""
    file = Path(path)
    if not file.is_file():
        return None
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AssetError(
            f"池子账本读不开：{file}（{exc}）",
            hint="它是本产品自己写的记账文件。坏掉之后**不猜着读**："
            "先把盘上那几个 .gts/.gtp 核一遍，再决定是删掉账本重装、还是手工处理",
        ) from exc
    if not isinstance(raw, dict):
        raise AssetError(
            f"池子账本不是一份对象（{type(raw).__name__}）：{file}",
            hint="本产品写的账本是一个 JSON 对象；这份不是",
        )
    fmt = raw.get("format")
    if fmt != MANIFEST_FORMAT:
        raise AssetError(
            f"池子账本格式版本是 {fmt!r}，这一版只认 {MANIFEST_FORMAT}：{file}",
            hint="版本对不上就不猜着读（与反向 Δ 同一个口径）；确认盘上文件后重装一次即可重写它",
        )
    rows = raw.get("added")
    if not isinstance(rows, list):
        raise AssetError(
            f"池子账本里没有 added 清单：{file}",
            hint="账本必须能说出「装了哪几个文件」，否则 remove 无从下手",
        )
    return PoolManifest(
        path=file,
        pool_dir=str(raw.get("pool_dir") or ""),
        entries=tuple(_manifest_entry_of(row, file) for row in rows),
    )


def _manifest_entry_of(row: Any, file: Path) -> ManifestEntry:
    """账本里一条的字段自证 —— 手改过的账本⛔不许把我们带出 StreamingAssets。"""
    if not isinstance(row, dict):
        raise AssetError(
            f"池子账本里有一条不是对象（{row!r}）：{file}",
            hint="一条必须是 {name, size, sha256}",
        )
    name = row.get("name")
    size = row.get("size")
    digest = row.get("sha256")
    if not isinstance(name, str) or not name or name in {".", ".."} or Path(name).name != name:
        raise AssetError(
            f"池子账本里的文件名不合法：{name!r}（{file}）",
            hint="只认**裸文件名**（不许带目录）—— 账本记什么就删什么，所以这里必须卡死",
        )
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise AssetError(
            f"池子账本里 {name} 的 size 不是非负整数：{size!r}（{file}）",
            hint="size 是账本自证的一部分，坏掉就拒绝读",
        )
    if not isinstance(digest, str) or len(digest) != 64:
        raise AssetError(
            f"池子账本里 {name} 的 sha256 不是 64 位十六进制：{digest!r}（{file}）",
            hint="sha256 是删不删的唯一判据，坏掉就拒绝读",
        )
    return ManifestEntry(name=name, size=size, sha256=digest)


def _records_of(path: Path) -> tuple[ManifestEntry, ...]:
    """账本里已有的条目（不在 ⇒ 空）；坏了就抛（正文里已经写清怎么处置）。"""
    manifest = load_manifest(path)
    return manifest.entries if manifest else ()


def _write_manifest(plan: PoolPlan, entries: Sequence[ManifestEntry]) -> bool:
    """写账本；内容与盘上**逐字相同**就不写（「没有新改动就不写盘」）。返回是否真写了。"""
    payload = {
        "format": MANIFEST_FORMAT,
        "tool": MANIFEST_TOOL,
        "pool_dir": str(plan.pool_dir),
        "added": [entry.to_json() for entry in entries],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    if plan.manifest_path.is_file():
        try:
            if plan.manifest_path.read_text(encoding="utf-8") == text:
                return False
        except OSError:  # pragma: no cover -- 读不到就当要写
            pass
    plan.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    plan.manifest_path.write_text(text, encoding="utf-8")
    return True


def _ordered(plan: PoolPlan, records: Mapping[str, ManifestEntry]) -> list[ManifestEntry]:
    """账本条目顺序：**本次计划里的先写**（与命令输出同序），其余按原样跟在后面。"""
    names = [entry.name for entry in plan.entries]
    rest = [name for name in records if name not in set(names)]
    return [records[name] for name in names + rest]


def _entry_from_record(record: ManifestEntry, dest: Path) -> PoolEntry:
    """账本里那一条 × 盘上现状。⛔ 期望值是**账本记的**，不是"盘上现在多大"。"""
    installed_size = dest.stat().st_size if dest.is_file() else None
    return PoolEntry(
        name=record.name,
        size=record.size,
        sha256=record.sha256,
        dest=dest,
        installed_size=installed_size,
        installed_sha256=sha256_of(dest) if installed_size is not None else None,
    )


def _copy_verified(src: Path, entry: PoolEntry) -> None:
    """拷一个文件 ＋ **写后复核 sha256**；复核不过就把半份删掉再抛（⛔ 不留半个）。"""
    try:
        shutil.copy2(src, entry.dest)
    except OSError as exc:
        raise AssetError(
            f"写不进去：{entry.dest}（{exc}）",
            hint="游戏在跑 / 目录只读 / 盘满了都会这样；先确认 StreamingAssets 能写",
        ) from exc
    got = sha256_of(entry.dest)
    if got != entry.sha256:
        entry.dest.unlink(missing_ok=True)
        raise AssetError(
            f"写后复核不过：{entry.name} 盘上是 {got}，源是 {entry.sha256}",
            hint="半份文件已经删掉；磁盘/权限的问题，重跑一次看是不是稳定复现",
        )


# --------------------------------------------------------------------------- #
# 页表（`.gts` = GRPG v6）**只读**：L6「在既有 group 里新增一个 item」的尺子
# --------------------------------------------------------------------------- #
#
# 为什么读数放这里：`vt_pool` 原来只认 name/size/sha256（搬文件用），对池子**内部**一无所知；
# 而「往既有 group 加一个 item」先要回答「这个组的地址空间还有没有空位」—— 那个答案只在 `.gts`
# 的页表里。本函数组**只读**（⛔ 一个字节都不写）；读数与判据见 `_work/l6/README.md`。
#
# 结构（本轮实测，逐条可复算）：
#
#   头 192 B：magic @0x00 · version @0x04 · id16 @0x0C · numLayers @0x1C · layersOff(u64) @0x20
#     · numLevels @0x28 · levelsOff(u64) @0x2C · tilePitch @0x34 · tileHeight @0x38 · border @0x3C
#     · maxTileSize @0x40 · numFlatTiles @0x44 · flatOff(u64) @0x48 · numRev @0x58
#     · revOff(u64) @0x5C · customPageSize @0x80 · numPageFiles @0x84 · 描述符表偏移(u32) @0x88
#   级别表：numLevels × 16 B = {nx, ny, tilesOffset, 0}，网格逐级减半；
#   瓦片数组：numLayers × nx × ny 个 u32，`0xFFFFFFFF` = 空槽；下标是 `[y][x][layer]`；
#   描述符表：numPageFiles × 544 B —— [0:64] 组名 UTF-16LE · [512] 常量 1 · [516:532] 对应
#     `.gtp` 头 8..23 的 16 B id · [532] 常量 2 · [536] 该分片字节数（四条实测都对上）。
#
# 哨兵：文件里 `FF FF FF FF` 的次数 / (文件字节数 / 4) —— 与 `_work/dig/tex/pool_inventory.py`
# 同一把尺子（主组实测 15.7751%，L6 卡片写的 15.78%）。

#: `.gts` 的魔数与版本（GRPG v6；别的版本**拒绝读**，不猜布局）。
GTS_MAGIC = b"GRPG"
GTS_VERSION = 6
#: 空槽哨兵。
GTS_SENTINEL = 0xFFFFFFFF
#: 头长度与描述符步长。
GTS_HEADER_BYTES = 192
DESCRIPTOR_BYTES = 544


@dataclass(frozen=True, slots=True)
class GtsLevel:
    """一级 mip 的页网格（`nx × ny` 页，每页 `numLayers` 个瓦片槽）。"""

    index: int
    nx: int
    ny: int
    tiles_offset: int
    slots: int
    nonempty: int
    content: int

    @property
    def px(self) -> tuple[int, int]:
        return (self.nx * self.content, self.ny * self.content)


@dataclass(frozen=True, slots=True)
class PageFileDescriptor:
    """描述符表里的一条（一个 `.gtp` 分片）：id 与字节数**实测**对得上那个文件。"""

    index: int
    offset: int
    name: str
    file_id: str
    size: int


@dataclass(frozen=True, slots=True)
class GtsTable:
    """一份 `.gts` 的**只读**读数（页表 ＋ 分片描述符）。"""

    path: Path
    size: int
    version: int
    group_id: str
    num_layers: int
    num_levels: int
    tile_pitch: int
    tile_height: int
    border: int
    max_tile_size: int
    num_flat: int
    flat_offset: int
    num_rev: int
    rev_offset: int
    custom_page_size: int
    levels: tuple[GtsLevel, ...]
    descriptors: tuple[PageFileDescriptor, ...]

    @property
    def content(self) -> int:
        """瓦片**内容**边长（去掉两侧 border）。"""
        return self.tile_pitch - 2 * self.border

    @property
    def box_pages(self) -> tuple[int, int]:
        """组地址空间 = level0 的页网格。"""
        return (self.levels[0].nx, self.levels[0].ny)

    @property
    def box_px(self) -> tuple[int, int]:
        return self.levels[0].px

    @property
    def nonempty_total(self) -> int:
        return sum(level.nonempty for level in self.levels)

    @property
    def readme_lines(self) -> tuple[str, ...]:
        """给人看的那几行（CLI 与用例共用同一份措辞）。"""
        nx, ny = self.box_pages
        wide, high = self.box_px
        match = "✅ 相符" if self.num_flat == self.nonempty_total else "❌ 不符"
        return (
            f"{self.path.name}  {self.size:,} B  GRPG v{self.version}",
            f"  层 {self.num_layers} · 级 {self.num_levels} · 瓦片 {self.content}px"
            f"（间距 {self.tile_pitch}，边 {self.border}）",
            f"  地址空间 {nx}×{ny} 页 = {wide}×{high} px"
            f" · customPageSize {self.custom_page_size:,}",
            f"  numFlatTiles {self.num_flat:,} vs Σ各级非空 {self.nonempty_total:,}（{match}）",
        )


def read_gts(path: str | Path) -> GtsTable:
    """读一份 `.gts`（**只读**）；结构不合法就抛 `AssetError`（⛔ 不返回「大概是这样」）。

    自证六条：魔数 / 版本 / 瓦片内容边长是 2 的幂 / 层数 1..8 与级数 1..16 / 级别表逐级减半且在
    文件内且互不重叠 / flat 与 rev 表在文件内 / 描述符表放得下声明的条数。**任何一条不过就抛**。
    """
    file = Path(path)
    try:
        raw = file.read_bytes()
    except OSError as exc:
        raise AssetError(f"读不到页表：{file}（{exc}）") from exc
    if len(raw) < GTS_HEADER_BYTES + 16:
        raise AssetError(
            f"{file.name} 只有 {len(raw):,} B，比一个 GRPG 头还短 —— 不是一份 .gts",
            hint="空文件 / 半份文件都会这样；⛔ 不许读成「0 个 item」",
        )
    if raw[:4] != GTS_MAGIC:
        raise AssetError(
            f"{file.name} 的魔数是 {raw[:4]!r}，不是 {GTS_MAGIC!r}",
            hint=".gts 的魔数必须是 GRPG；.gtp 是 GRAP（两个别混）",
        )
    version = struct.unpack_from("<I", raw, 4)[0]
    if version != GTS_VERSION:
        raise AssetError(
            f"{file.name} 的页表版本是 {version}，本版只认 {GTS_VERSION}",
            hint="版本号不对就**拒绝读**（不猜新布局）",
        )

    def u32(offset: int) -> int:
        return struct.unpack_from("<I", raw, offset)[0]

    def u64(offset: int) -> int:
        return struct.unpack_from("<Q", raw, offset)[0]

    num_layers = u32(0x1C)
    num_levels = u32(0x28)
    tile_pitch = u32(0x34)
    border = u32(0x3C)
    content = tile_pitch - 2 * border
    if content <= 0 or (content & (content - 1)) != 0:
        raise AssetError(
            f"瓦片内容边长 = {tile_pitch} - 2×{border} = {content}，不是 2 的幂 ⇒ 头部布局不对",
            hint="这一条是「读对位置」的机械判据（真件 128 = 144 - 2×8）",
        )
    if not 1 <= num_layers <= 8:
        raise AssetError(f"numLayers={num_layers} 不合理（1..8）")
    if not 1 <= num_levels <= 16:
        raise AssetError(f"numLevels={num_levels} 不合理（1..16）")

    levels_off = u64(0x2C)
    if levels_off + 16 * num_levels > len(raw):
        raise AssetError(
            f"级别表越界：0x{levels_off:X} + 16×{num_levels} > 文件 {len(raw):,} B",
            hint="截断的文件走这里（负控）",
        )
    levels: list[GtsLevel] = []
    seen_offsets: set[int] = set()
    for index in range(num_levels):
        offset = levels_off + 16 * index
        nx, ny, tiles_off, _tail = struct.unpack_from("<4I", raw, offset)
        if nx == 0 or ny == 0:
            raise AssetError(f"level{index} 的页网格是 {nx}×{ny}")
        if index and (nx * 2 != levels[-1].nx or ny * 2 != levels[-1].ny):
            raise AssetError(
                f"level{index} 网格 {nx}×{ny} 不是上一级（{levels[-1].nx}×{levels[-1].ny}）的一半"
            )
        slots = num_layers * nx * ny
        if tiles_off + 4 * slots > len(raw):
            raise AssetError(
                f"level{index} 瓦片数组越界：0x{tiles_off:X} + {4 * slots:,} > {len(raw):,} B"
            )
        if tiles_off in seen_offsets:
            raise AssetError(f"level{index} 的瓦片数组与别的级别共用同一段（0x{tiles_off:X}）")
        seen_offsets.add(tiles_off)
        arr = struct.unpack_from(f"<{slots}I", raw, tiles_off)
        levels.append(
            GtsLevel(
                index=index,
                nx=nx,
                ny=ny,
                tiles_offset=tiles_off,
                slots=slots,
                nonempty=sum(1 for value in arr if value != GTS_SENTINEL),
                content=content,
            )
        )
    num_flat = u32(0x44)
    flat_off = u64(0x48)
    num_rev = u32(0x58)
    rev_off = u64(0x5C)
    if flat_off + 4 * num_flat > len(raw):
        raise AssetError(f"flat 表越界：0x{flat_off:X} + 4×{num_flat:,} > {len(raw):,} B")
    if rev_off + 4 * num_rev > len(raw):
        raise AssetError(f"rev 表越界：0x{rev_off:X} + 4×{num_rev:,} > {len(raw):,} B")

    # 描述符表：它把「页表」与盘上那几个 .gtp 绑在一起（分片 id / 字节数）
    pdb_off = u32(0x88)
    num_page_files = u32(0x84)
    if not 1 <= num_page_files <= 16:
        raise AssetError(f"numPageFiles={num_page_files} 不合理（1..16）")
    if pdb_off + DESCRIPTOR_BYTES > len(raw):
        raise AssetError(f"描述符表偏移 0x{pdb_off:X} 越出文件（{len(raw):,} B）")
    room = (len(raw) - pdb_off) // DESCRIPTOR_BYTES
    if room < num_page_files:
        raise AssetError(
            f"描述符表声明 {num_page_files} 条，文件里只放得下 {room} 条"
            f"（@0x{pdb_off:X}，文件 {len(raw):,} B）",
            hint="numPageFiles 与文件长度对不上 ⇒ 头部或描述符表读错了（⛔ 不静默截断）",
        )
    descriptors: list[PageFileDescriptor] = []
    for index in range(num_page_files):
        offset = pdb_off + index * DESCRIPTOR_BYTES
        blob = raw[offset : offset + DESCRIPTOR_BYTES]
        descriptors.append(
            PageFileDescriptor(
                index=index,
                offset=offset,
                name=blob[:64].decode("utf-16-le", "replace").split(chr(0))[0],
                file_id=blob[516:532].hex(),
                size=struct.unpack_from("<Q", blob, 536)[0],
            )
        )

    return GtsTable(
        path=file,
        size=len(raw),
        version=version,
        group_id=raw[0x0C:0x1C].hex(),
        num_layers=num_layers,
        num_levels=num_levels,
        tile_pitch=tile_pitch,
        tile_height=u32(0x38),
        border=border,
        max_tile_size=u32(0x40),
        num_flat=num_flat,
        flat_offset=flat_off,
        num_rev=num_rev,
        rev_offset=rev_off,
        custom_page_size=u32(0x80),
        levels=tuple(levels),
        descriptors=tuple(descriptors),
    )


@dataclass(frozen=True, slots=True)
class SentinelReading:
    """哨兵读数（L6 卡片那把尺子）：`FF FF FF FF` 次数 / (字节数 / 4)。"""

    path: Path
    size: int
    entries: int
    sentinel: int

    @property
    def ratio(self) -> float:
        return self.sentinel / self.entries if self.entries else 0.0

    @property
    def line(self) -> str:
        return f"  哨兵 {self.sentinel:,} / {self.entries:,} = {100.0 * self.ratio:.4f}%"


def gts_sentinel(path: str | Path) -> SentinelReading:
    """数 `FF FF FF FF`（与 `_work/dig/tex/pool_inventory.py` **同一把尺子**）。"""
    file = Path(path)
    raw = file.read_bytes()
    return SentinelReading(
        path=file,
        size=len(raw),
        entries=len(raw) // 4,
        sentinel=raw.count(b"\xff\xff\xff\xff"),
    )


@dataclass(frozen=True, slots=True)
class FreeRect:
    """地址空间里一块**全空**的矩形（页坐标）。"""

    x: int
    y: int
    w: int
    h: int
    content: int

    @property
    def pages(self) -> int:
        return self.w * self.h

    @property
    def px(self) -> tuple[int, int]:
        return (self.w * self.content, self.h * self.content)


def _histogram_rects(heights: Sequence[int]) -> list[tuple[int, int, int]]:
    """直方图里的**极大**矩形：返回 (高, 左, 右) 候选（单调栈）。"""
    stack: list[int] = []
    out: list[tuple[int, int, int]] = []
    for index in range(len(heights) + 1):
        cur = heights[index] if index < len(heights) else 0
        while stack and heights[stack[-1]] >= cur:
            top = stack.pop()
            left = stack[-1] + 1 if stack else 0
            out.append((heights[top], left, index))
        stack.append(index)
    return out


#: `level0_free_rects` 的规模上限（页数）：超了就**不猜**，直接说「没算」（读数不许含糊）。
FREE_RECT_PAGE_CAP = 4_000_000


def level0_free_rects(table: GtsTable, *, top: int = 3) -> tuple[FreeRect, ...]:
    """level0 里最大的几块全空矩形 —— 「往这个组加 item」能落在哪。

    读法：level0 的瓦片数组按 `[y][x][layer]`；一个**页**空 = 它每一层都是哨兵。
    算法：逐行直方图 ＋ 单调栈（最大全空矩形）。超过 `FREE_RECT_PAGE_CAP` 页 ⇒ 返回空元组
    （调用方必须把「没算」与「没有空位」分开说）。
    """
    level0 = table.levels[0]
    nx, ny, layers = level0.nx, level0.ny, table.num_layers
    if nx * ny > FREE_RECT_PAGE_CAP:
        return ()
    raw = table.path.read_bytes()
    arr = struct.unpack_from(f"<{level0.slots}I", raw, level0.tiles_offset)
    found: list[FreeRect] = []
    heights = [0] * nx
    for y in range(ny):
        base = y * nx * layers
        for x in range(nx):
            at = base + x * layers
            heights[x] = heights[x] + 1 if all(
                arr[at + k] == GTS_SENTINEL for k in range(layers)
            ) else 0
        for height, left, right in _histogram_rects(heights):
            if height:
                found.append(
                    FreeRect(
                        x=left,
                        y=y - height + 1,
                        w=right - left,
                        h=height,
                        content=level0.content,
                    )
                )
    found.sort(key=lambda rect: (-rect.pages, rect.y, rect.x))
    out: list[FreeRect] = []
    for rect in found:
        covered = False
        for keep in out:
            if (
                rect.x >= keep.x
                and rect.y >= keep.y
                and rect.x + rect.w <= keep.x + keep.w
                and rect.y + rect.h <= keep.y + keep.h
            ):
                covered = True
                break
        if covered:
            continue
        out.append(rect)
        if len(out) >= top:
            break
    return tuple(out)


@dataclass(frozen=True, slots=True)
class ShardFile:
    """盘上的一个 `.gtp` 分片：名字 ＋ 字节数 ＋ 头里那 16 B id。"""

    name: str
    size: int
    file_id: str


#: `.gtp` 的魔数与版本（GRAP v4）—— 只读它的头 24 B，不读载荷。
GTP_MAGIC = b"GRAP"
GTP_HEADER_BYTES = 24


def gtp_shards(streaming_dir: str | Path) -> tuple[ShardFile, ...]:
    """一个目录里所有 `.gtp` 分片的（名字, 字节数, 头 id）—— 只读头 24 B。"""
    directory = Path(streaming_dir)
    if not directory.is_dir():
        raise AssetError(f"没有这个目录：{directory}")
    out: list[ShardFile] = []
    for path in sorted(directory.glob("*.gtp")):
        with path.open("rb") as handle:
            head = handle.read(GTP_HEADER_BYTES)
        if head[:4] != GTP_MAGIC:
            raise AssetError(
                f"{path.name} 的魔数是 {head[:4]!r}，不是 {GTP_MAGIC!r}",
                hint=".gtp 的魔数必须是 GRAP（.gts 是 GRPG）—— 别拿错文件",
            )
        out.append(ShardFile(name=path.name, size=path.stat().st_size, file_id=head[8:24].hex()))
    return tuple(out)


def descriptor_lines(table: GtsTable, shards: Sequence[ShardFile]) -> tuple[str, ...]:
    """把页表里的分片描述符与**盘上那几个 `.gtp`** 对上（id ＋ 字节数），逐条印。"""
    by_id = {shard.file_id: shard for shard in shards}
    lines: list[str] = []
    for descriptor in table.descriptors:
        shard = by_id.get(descriptor.file_id)
        if shard is None:
            lines.append(
                f"  ❌ 分片[{descriptor.index}] id={descriptor.file_id[:16]}…"
                f" 页表记 {descriptor.size:,} B —— 盘上没有这个 id 的 .gtp"
            )
        elif shard.size != descriptor.size:
            lines.append(
                f"  ❌ 分片[{descriptor.index}] {shard.name} 页表记 {descriptor.size:,} B"
                f" · 盘上 {shard.size:,} B"
            )
        else:
            lines.append(
                f"  ✅ 分片[{descriptor.index}] {shard.name}  {shard.size:,} B"
                f"   id={descriptor.file_id[:16]}…（页表记的字节数与盘上**逐字节相符**）"
            )
    return tuple(lines)


def pool_tables(streaming_dir: str | Path) -> tuple[GtsTable, ...]:
    """一个目录里所有 `.gts` 的读数（按名字排序）。读不了就抛（⛔ 不跳过）。"""
    directory = Path(streaming_dir)
    if not directory.is_dir():
        raise AssetError(f"没有这个目录：{directory}")
    return tuple(read_gts(path) for path in sorted(directory.glob("*.gts")))


@dataclass(frozen=True, slots=True)
class PoolStats:
    """`vt pool stats` 的读数（**只读**）：逐组的页表 ＋ 哨兵 ＋ 最大全空矩形。"""

    streaming_dir: Path
    tables: tuple[GtsTable, ...]
    sentinels: tuple[SentinelReading, ...]
    free: tuple[tuple[FreeRect, ...], ...]
    shards: tuple[ShardFile, ...] = ()

    def criterion_lines(self) -> tuple[str, ...]:
        lines = [f"[VT 页表 · 读] {self.streaming_dir}"]
        for index, table in enumerate(self.tables):
            sentinel = self.sentinels[index]
            rects = self.free[index]
            level0 = table.levels[0]
            lines.extend(table.readme_lines)
            lines.append(sentinel.line)
            pages = level0.slots // max(1, table.num_layers)
            occupied = level0.nonempty // max(1, table.num_layers)
            lines.append(
                f"  level0 槽 {level0.slots:,} · 非空 {level0.nonempty:,}"
                f"（{100.0 * level0.nonempty / max(1, level0.slots):.2f}%）"
                f"· 空页 {pages - occupied:,} / {pages:,}"
            )
            if rects:
                biggest = rects[0]
                wide, high = biggest.px
                where = " · ".join(f"{r.w}×{r.h}@({r.x},{r.y})" for r in rects)
                lines.append(
                    f"  最大全空矩形 {biggest.w}×{biggest.h} 页 = {wide}×{high} px"
                    f" @页({biggest.x},{biggest.y})；前 {len(rects)} 块：{where}"
                )
            elif pages > FREE_RECT_PAGE_CAP:
                lines.append(
                    f"  最大全空矩形：**没算**（{pages:,} 页超过上限 {FREE_RECT_PAGE_CAP:,}）"
                )
            else:
                lines.append("  最大全空矩形：**没有**（level0 一个空页都没有）")
            lines.extend(descriptor_lines(table, self.shards))
        return tuple(lines)


def pool_stats(target: str | Path) -> PoolStats:
    """**只读**：给一个目录（`StreamingAssets` 或游戏根）出逐组页表读数。

    `target` 里没有 `.gts` 时按**游戏根**处理（走 `vt_stack.streaming_dir_of`，与其它入口同口径）。
    """
    directory = Path(target)
    if not list(directory.glob("*.gts")):
        directory = vt_stack.streaming_dir_of(target)
    tables = pool_tables(directory)
    sentinels = tuple(gts_sentinel(table.path) for table in tables)
    free = tuple(level0_free_rects(table) for table in tables)
    return PoolStats(
        streaming_dir=directory,
        tables=tables,
        sentinels=sentinels,
        free=free,
        shards=gtp_shards(directory),
    )
