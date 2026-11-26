"""外部库 `BA_Mod.zip` 的就地安装 / 反向 Δ 还原（零 Qt）。

## 这条通道是什么

ADR 0005 定的**主通道**：把一份 24 张表明文的 zip 放到 `<游戏根>/BA_Mod.zip`，游戏就从它
加载数据库（约 300 KB / 毫秒级，而不是重写 7.1 GB 的 `data.unity3d`）。
**删掉那个文件即卸载**（DLL 补丁可以一直留着，见 `core/asset/dllpatch.py`）。

ADR 0007 第 4 条接着定「怎么撤」：⛔ **不许拷整包当备份**（用户原话：垃圾备份文件占硬盘），
改用**反向 Δ**。`BA_Mod.zip` 在补丁里是一个**整文件目标**（`FileTarget`）：

* 装前就有别人的包 → 补丁里存**要盖回去的那些旧字节**（段级 Δ；长度变了回退整份），
  还原 = 逐位盖回去；
* 装前没有 → 只记 `existed=False`，还原 = **删掉它**。

补丁落**工程目录**的 `patches/`（一次安装一条、再装覆盖它、还原成功即删），与容器 / bundle
的 Δ 共用同一个补丁柜（`asset_kernel/reverse_delta.py`）。

## 四个动作

* :func:`status` —— 只读读数：zip 在不在 / 大小 / sha256；给了 `project_root` 再报补丁那条；
* :func:`install` —— 记反向 Δ（`PatchStore`）→ `commit()` → 写 `<zip>.tmp` →
  `os.replace` → 复核落点；
* :func:`restore` —— 逐位还原 + 成功即删补丁；**再撤一次不报错**（幂等）；
* :func:`remove` —— **卸载**：把 zip 从游戏根删掉（模块头第 1 节那句「删掉那个文件即卸载」）
  ＋ 留可还原条目。⛔ 不是 `restore` —— 那个还原的是「**装之前**那一份」，
  而这里要的是「**这个文件不存在**」那个状态（2026-10-12 现场：装之前那 326,821 B
  本身就带 3 行测试单位，拿它当「回原版」是错的）。

### 卸载为什么是「搬」而不是「拷」

ADR 0007 第 4 条：⛔ 不许拷整包当备份（用户原话：垃圾备份文件占硬盘）。所以 `remove`
把文件**搬**到 `<工程目录>/removed/` 并记 `removed.json`（原路径 / 字节 / sha256 / 搬去哪）——
搬不产生第二份字节，游戏根里也不留任何 `.bak`。同盘守卫沿用红线 2（`assert_same_drive`）。

## 装盘顺序 ＝ **先落补丁，再写盘**（Lead 定口径，与写回器 Δ 那条链一致）

    ① 记基线：capture_file（内存，不写盘）
    ② commit()：补丁落 <工程目录>/patches
    ③ 写 <zip>.tmp -> 复核 -> os.replace -> 再复核落点
    ④ ③ 出事：store.rollback() 撤回（**唯一一份**实现在 reverse_delta.PatchStore），
       撤销结果写进报告并跟原错一起抛（不吞）

为什么**不**反过来（先写盘、后 commit）：崩在两者之间就是「游戏根被改、补丁没落盘」——
**原字节彻底没了**。按上面这个顺序，崩在哪一步都有退路：② 之前崩 ⇒ 文件还是装前那份；
② 之后崩 ⇒ 原字节已经在补丁里（文件仍等于装前时 restore 走「already」把补丁清掉，无害；
真写了一半时 restore 会因为 sha 不等于「装后」而**拒绝** —— 这是故意的，不许静默覆盖）。

## 三道红线（沿用写回器口径，别在这里另立一套）

1. **游戏在跑就拒绝**（`core.assets.game.is_game_running`）—— 装与撤**都拦**；干跑也拦，
   否则那份「判据一行」描述的是一次注定要失败的写。
2. **补丁柜跟随游戏盘**：`PatchStore(root, game_root=...)` 自带 `assert_same_drive`，
   本模块**不再写第二份**（历史教训：备份落 C 盘白占 6.95 GB）。
3. **写完复核**：替换完把落点读回来比 sha256；对不上就走 ④ 撤回，不许留下一个与补丁对不上的
   半成品（一条与磁盘现状对不上的补丁，下次还原会拒绝或者误伤）。

## 干跑（`dry_run=True`）一个字节都不写

它照样跑一遍 `begin -> capture_file`（全在内存里），所以：

* 「合并基线与磁盘对不上」这类拒绝会**提前**暴露（`capture_file` 自己就拦）；
* 判据一行里的 sha256 前后与补丁体积**都是真算出来的**，不是猜的。

判据一行（可复算）：`.venv\\Scripts\\python.exe -m pytest tests/test_external_db.py -q`
→ 通过用例数 **从 0（文件不存在）变 11 passed**；其中正控 = 装完手改 1 字节 ⇒ 还原必须拒绝
（AGENTS §0.9 动作 2：尺子先能红）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel.reverse_delta import (
    FileTarget,
    PatchStore,
    RestoreReport,
    assert_same_drive,
    patch_store_root,
    sha256_of,
)
from ba_mod_maker.core.asset import dbzip
from ba_mod_maker.core.errors import AssetError, DataSourceError
from ba_mod_maker.core.session import PACKAGE_DIR, PACKAGE_NAME

__all__ = [
    "INSTALL_TMP_SUFFIX",
    "REMOVED_DIRNAME",
    "REMOVED_RECORD",
    "ExternalDbStatus",
    "InstallReport",
    "RemoveReport",
    "RemovedFile",
    "UnremoveReport",
    "game_zip_path",
    "install",
    "read_removed_record",
    "remove",
    "removed_root",
    "restore",
    "unremove",
    "status",
]

#: 落盘用的临时文件后缀：先写 `<zip>.tmp` 再 `os.replace`（与 `session._save_dbzip` 同口径）。
#: 失败路径上它**一定被删掉** —— 它长得像备份，留着既占地方又会被误当备份。
INSTALL_TMP_SUFFIX = ".tmp"

#: 干跑时给补丁 json 表留的余量（每条目标约 0.5 KB）。blob 才是大头，且是**精确值**：
#: `PatchBuilder.targets` 的 `stored_bytes` 与 blob 里的字节一一对应。
_PATCH_JSON_ALLOWANCE = 1024


def game_zip_path(game_root: str | Path) -> Path:
    """`<游戏根>/BA_Mod/BA_Mod.zip`（2026-10-27 起：数据库和资产包一起收在 `BA_Mod\\` 里）。

    文件名 / 子目录名**只有一处**：`core/session.py::PACKAGE_NAME` / `PACKAGE_DIR`
    （DLL 补丁那边从 `PACKAGE_REL_PATH` 取同一串，见 `core/asset/dllpatch.py::build_cave`）。
    两边各写一个字面量的话，症状是「包装好了、游戏还是编译库」。
    """
    return Path(game_root) / PACKAGE_DIR / PACKAGE_NAME


def ensure_zip_dir(game_root: str | Path) -> Path:
    """把 `<游戏根>/BA_Mod\\` 造出来（幂等）并返回它 —— **只有写盘前才调**，读现场不许调。"""
    folder = Path(game_root) / PACKAGE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _same_file(a: Path, b: Path) -> bool:
    """两个路径是不是同一个文件（Windows 上大小写不敏感，所以用 normcase 比）。"""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# --------------------------------------------------------------------------- #
# 读数
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ExternalDbStatus:
    """`<游戏根>/BA_Mod.zip` 的现场读数。`status()` 是**纯只读**，不碰任何文件。"""

    game_root: Path
    zip_path: Path
    #: 那个 zip 在不在（不在 = 游戏走编译库 data.unity3d，等于没装）。
    present: bool
    size: int = 0
    sha256: str = ""
    #: 下面几项要给了 `project_root` 才有 —— 补丁柜在工程目录里，游戏根里查不到。
    patch_dir: Path | None = None
    has_patch: bool = False
    patch_label: str = ""
    patch_created: str = ""
    patch_stored_bytes: int = 0
    #: 补丁记的「装后」与磁盘现状对得上吗（None = 补丁不是冲这个文件来的）。
    patch_matches_disk: bool | None = None
    #: 补丁自己那几行人话（`InstallPatch.lines()`）。
    patch_lines: tuple[str, ...] = ()

    def lines(self) -> tuple[str, ...]:
        """给人看的几行（**CLI 直接逐行打印**；GUI 共用同一套措辞，免得两处对不上）。"""
        if self.present:
            head = f"{self.zip_path.name}：有（{self.size:,} 字节，sha256 {self.sha256[:16]}…）"
        else:
            head = f"{self.zip_path.name}：没有（游戏会走编译库 data.unity3d）"
        out = [f"游戏目录   : {self.game_root}", f"外部库     : {head}"]
        if self.patch_dir is None:
            out.append("未还原补丁 : 未查（没给工程目录）")
            return tuple(out)
        if not self.has_patch:
            out.append(f"未还原补丁 : 没有（{self.patch_dir}）")
            return tuple(out)
        out.append(
            f"未还原补丁 : 有 —— {self.patch_label or '（无标签）'}"
            f"（{self.patch_created}，{self.patch_stored_bytes:,} 字节）"
        )
        mark = {
            True: "对得上",
            False: "对不上（装完之后被别的改动动过）",
            None: "不是冲这个文件来的",
        }
        out.append(f"补丁与磁盘 : {mark[self.patch_matches_disk]}")
        return tuple(out)


def status(game_root: str | Path, *, project_root: str | Path | None = None) -> ExternalDbStatus:
    """读现场：zip 在不在 / 大小 / sha256；给了 `project_root` 再读补丁那条。**只读。**

    `project_root` 为什么不是位置参数：补丁柜是**工程目录**的东西，而很多调用方只是想问
    「游戏根里那份包在不在」—— 那种问题不该逼它知道工程目录在哪。
    """
    root = Path(game_root)
    zip_path = game_zip_path(root)
    st = ExternalDbStatus(game_root=root, zip_path=zip_path, present=zip_path.is_file())
    if st.present:
        st.size = zip_path.stat().st_size
        st.sha256 = sha256_of(zip_path)
    if project_root is None:
        return st

    store = PatchStore(patch_store_root(project_root))
    st.patch_dir = store.root
    st.has_patch = store.has_pending()
    patch = store.pending() if st.has_patch else None
    if patch is None:
        return st
    st.patch_label = patch.label
    st.patch_created = patch.created
    st.patch_stored_bytes = patch.stored_bytes
    st.patch_lines = patch.lines()
    for target in patch.targets:
        if isinstance(target, FileTarget) and _same_file(target.path, zip_path):
            st.patch_matches_disk = st.present and st.sha256 == target.sha_after
            break
    return st


# --------------------------------------------------------------------------- #
# 安装
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class InstallReport:
    """一次安装的读数（干跑只填「打算做什么」，**一个字节都不写**）。"""

    game_root: Path
    zip_path: Path
    source_zip: Path
    dry_run: bool
    #: "create"（原本没有）/ "replace"（覆盖一份已有的）/ "same"（落点已经是这份内容）。
    action: str
    label: str = ""
    existed_before: bool = False
    size_before: int = 0
    sha_before: str = ""
    size_after: int = 0
    sha_after: str = ""
    #: 补丁体积：真装 = 落盘后两个文件的**实测**大小；干跑 = **估算**
    #: （blob 那部分是精确值 —— 与各目标的 stored_bytes 一一对应，再加 json 表的余量）。
    patch_bytes: int = 0
    patch_estimated: bool = False
    patch_path: Path | None = None
    patch_lines: tuple[str, ...] = ()
    #: 写后复核过了吗（落点 sha256 == 装后）。干跑与「落点已经是这份」都不走复核 ⇒ False。
    verified: bool = False
    #: 写盘出事时用补丁撤回的结果（人话 + 成功与否）—— 也跟着原错一起抛，不吞。
    rollback_line: str = ""
    rollback_ok: bool = False

    @property
    def installed(self) -> bool:
        """这份报告是不是一次**真的落了盘**的安装。"""
        return not self.dry_run and self.action in ("create", "replace")

    @property
    def state_label(self) -> str:
        if self.action == "same":
            return "落点已经是这份内容，未写盘"
        if self.dry_run:
            return "干跑（一个字节都没写）"
        return "已安装" if self.action == "create" else "已覆盖安装"

    def criteria_line(self) -> str:
        """**判据一行**：目标路径 + 大小 + sha256 前后 + 补丁体积。

        可复算：这里的 `sha_after` == `sha256(source_zip)`；真装完
        `status(game_root).sha256` 与它相等；还原之后逐位 == 装之前那份（或文件不存在）。
        """
        before = (
            f"装前 {self.size_before:,} B / sha256 {self.sha_before[:16]}…"
            if self.existed_before
            else "装前 不存在"
        )
        after = f"装后 {self.size_after:,} B / sha256 {self.sha_after[:16]}…"
        how = "估算" if self.patch_estimated else "实测"
        where = "" if self.patch_path is None else f" → {self.patch_path}"
        return f"{self.zip_path}：{before}；{after}；反向 Δ {self.patch_bytes:,} B（{how}{where}）"

    def lines(self) -> tuple[str, ...]:
        """人读的读数：目标路径 / 大小 / sha256 前后 / 补丁体积 / 写后复核结果。"""
        out = [f"[外部库] {self.state_label}", "  " + self.criteria_line()]
        if self.dry_run:
            out.append("  未写盘（dry_run）：反向 Δ 那段是**估算**，真装去掉 dry_run")
        elif self.action == "same":
            out.append("  复核：无需复核（落点与源包逐位相同，没有写盘动作）")
        else:
            mark = "✓ 落点 sha256 == 装后" if self.verified else "✗ 没走复核"
            out.append(f"  写后复核：{mark}")
        out.extend("  " + line for line in self.patch_lines)
        if self.rollback_line:
            out.append("  " + self.rollback_line)
        return tuple(out)


def _check_game_not_running(action: str) -> None:
    """红线 1：游戏在跑就拒绝（写游戏目录之前的第一件事）。"""
    from ba_mod_maker.core.assets import game as game_mod

    if game_mod.is_game_running():
        raise AssetError(
            f"游戏正在运行，无法{action}外部库 {PACKAGE_NAME}",
            hint="游戏占着游戏目录里的文件时写入会失败（WinError 5）。请先退出游戏。",
        )


def _read_source_package(source: Path) -> bytes:
    """读要装的那份包，并先确认它**真的是一份外部库**（按内容认，不看文件名）。"""
    if not source.is_file():
        raise AssetError(
            f"找不到要装的包：{source}",
            hint=f"要装的是导出出来的 {PACKAGE_NAME}（例：工程目录里那一份）。",
        )
    try:
        dbzip.DbZip.read(source)
    except DataSourceError as exc:
        raise AssetError(
            f"要装的那份不是能用的外部库：{source}（{exc.message}）",
            hint=exc.hint,
        ) from exc
    return source.read_bytes()


def install(
    game_root: str | Path,
    source_zip: str | Path,
    *,
    project_root: str | Path,
    dry_run: bool = False,
    label: str = "",
    check_game_running: bool = True,
) -> InstallReport:
    """把一份 `BA_Mod.zip` **就地装进游戏根**，并留下反向 Δ（ADR 0007 第 4 条）。

    参数
    ----
    game_root
        游戏根目录（含 `BrokenArrow_Data` 的那一层）。**必须已经存在** ——
        本模块不替用户造目录（路径打错时会造出一个奇怪的目录，比报错难查得多）。
    source_zip
        要装的那份包（一般是工程目录里导出的 `BA_Mod.zip`）。装之前按**内容**验一遍，
        免得把不认识的文件写进游戏根。
    project_root
        工程目录：补丁柜落在 `<project_root>/patches`，**必须与游戏同盘**（红线 2）。
    dry_run
        干跑：算得出一份真读数的报告，但**一个字节都不写**（连 `patches/` 都不建）。
    label
        补丁的标签（例：`'图标键：Units[73].ThumbnailFileName'`）。省略 = 按源文件名生成。
    check_game_running
        写前检测游戏进程（默认开）。只有用例会关它。

    顺序见模块头部：「**先落补丁、再写盘**」；写盘出事就用补丁撤回，撤销结果写进报告
    并跟着原错一起抛（`raise ... from exc`，`exc.context["report"]` 就是这份报告）。
    """
    root = Path(game_root)
    zip_path = game_zip_path(root)
    source = Path(source_zip)

    if check_game_running:
        _check_game_not_running("安装")
    if not root.is_dir():
        raise AssetError(
            f"游戏根目录不存在：{root}",
            hint="选含 BrokenArrow_Data 的那一层；本模块不会替你把目录造出来。",
        )
    if _same_file(source, zip_path):
        raise AssetError(
            f"源文件与落点是同一个：{source}",
            hint=(
                f"要装的是**别处**那份 {PACKAGE_NAME}（例：工程目录里导出的），"
                "不能拿落点自己装自己。"
            ),
        )

    after = _read_source_package(source)
    sha_after = _sha256(after)
    before = zip_path.read_bytes() if zip_path.is_file() else None

    report = InstallReport(
        game_root=root,
        zip_path=zip_path,
        source_zip=source,
        dry_run=dry_run,
        action="create" if before is None else "replace",
        label=label or f"外部库 {PACKAGE_NAME}（{source.name}）",
        existed_before=before is not None,
        size_before=0 if before is None else len(before),
        sha_before="" if before is None else _sha256(before),
        size_after=len(after),
        sha_after=sha_after,
    )
    if before is not None and before == after:
        # 落点已经是这份内容：**不记补丁** —— 记了就是把「自己」当基线，
        # 反而多一条能误伤别人改动的退路。
        report.action = "same"
        return report

    store = PatchStore(patch_store_root(project_root), game_root=root)
    builder = store.begin(report.label)
    # capture_file 两种情形都会把「装后」写成 after 的 sha256/size：
    # 新目标直接填；命中已有 FileTarget（再装覆盖它）时**基线保持最早那份**、只更新装后
    # （reverse_delta.py::PatchBuilder.capture_file 的合并分支）—— 所以这里不必再补 set_after。
    builder.capture_file(zip_path, before=before, after=after)
    report.patch_path = store.json_path

    if dry_run:
        # blob 里的字节与各目标的 stored_bytes 一一对应 ⇒ 这个和是**精确值**，不是拍脑袋。
        blob = sum(t.stored_bytes for t in builder.targets)
        report.patch_bytes = blob + _PATCH_JSON_ALLOWANCE
        report.patch_estimated = True
        return report

    # ① 先落补丁：崩在下面任何一步，原字节都还在补丁里（顺序的理由见模块头部）。
    builder.commit()
    patch = store.pending()
    if patch is not None:
        report.patch_lines = patch.lines()
    if store.json_path.is_file() and store.blob_path.is_file():
        report.patch_bytes = store.json_path.stat().st_size + store.blob_path.stat().st_size

    # ② 再写盘：临时文件 -> 复核 -> 原子替换 -> 再复核落点。
    #    落点在 `<游戏根>/BA_Mod\` 里（2026-10-27 起）—— 目录不在就造出来；
    #    ⛔ 只在这一步造，`status()` / `game_zip_path()` 这类读现场的函数一个目录都不许建。
    ensure_zip_dir(root)
    tmp = zip_path.with_name(zip_path.name + INSTALL_TMP_SUFFIX)
    try:
        tmp.write_bytes(after)
        if tmp.read_bytes() != after:
            raise AssetError(
                f"写后复核不通过：临时文件与预期不一致（{tmp}）",
                hint="落点未被替换，原来那份还在。请重试；反复出现就把日志报上来。",
            )
        os.replace(tmp, zip_path)
        if sha256_of(zip_path) != sha_after:
            raise AssetError(
                f"写后复核不通过：{zip_path.name} 的内容与预期不符",
                hint="下面那句自动撤销的结果才是现在的现场。",
            )
        report.verified = True  # 落点读回来 sha256 == 装后（上面那一句比对过了）
    except BaseException as exc:  # 出事必须清临时文件 + 用补丁撤回，再把原错抛出去
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        ok, undone = store.rollback()  # 唯一一份实现在 reverse_delta.PatchStore（不再本地复制）
        report.rollback_ok = ok
        report.rollback_line = undone
        if ok:
            report.patch_path = None  # 撤回成功 = 补丁已被 store.restore() 删掉
        hint = exc.hint if isinstance(exc, AssetError) else "看上一句撤销结果"
        raise AssetError(
            f"装 {zip_path.name} 失败：{exc}（{undone}）",
            hint=hint,
            report=report,
        ) from exc
    return report


# --------------------------------------------------------------------------- #
# 还原
# --------------------------------------------------------------------------- #
def restore(
    project_root: str | Path,
    *,
    game_root: str | Path | None = None,
    check_game_running: bool = True,
) -> RestoreReport:
    """按工程目录里的补丁**逐位还原**，成功即删补丁。返回 `RestoreReport`。

    * **幂等**：没有未还原的补丁（从没装过 / 已经撤过一次）不报错，返回一份 ok 的报告 ——
      「撤销」这个动作本来就应该点几次都一样；
    * **两把锁在补丁那一侧**（`reverse_delta.InstallPatch._restore_file`）：磁盘现状必须等于
      补丁的「装后」才动手，重放出来的原字节要先自证 sha256 才替换 ⇒ 文件被动过就**拒绝**，
      而不是覆盖掉用户的改动；
    * `game_root` 只用来做**同盘守卫**（红线 2）；补丁里记的目标路径是绝对路径，还原不需要它。
    """
    if check_game_running:
        _check_game_not_running("还原")
    store = PatchStore(patch_store_root(project_root), game_root=game_root)
    if not store.has_pending():
        report = RestoreReport(label="")
        report.add(
            {
                "kind": "none",
                "ok": True,
                "skipped": "no_patch",
                "detail": f"没有未还原的补丁，无需还原（{store.root}）",
            }
        )
        return report
    return store.restore()


# --------------------------------------------------------------------------- #
# 卸载：删文件 ＋ 留可还原条目（⚠ 不是 restore —— 见模块头那一节）
# --------------------------------------------------------------------------- #
#: 卸载时那些文件搬到哪（相对 project_root；与补丁柜 `patches/` 平级）。
REMOVED_DIRNAME = "removed"

#: 卸载台账的文件名（住在 `removed/` 里）。
REMOVED_RECORD = "removed.json"


def removed_root(project_root: str | Path) -> Path:
    """卸载去向 = `<工程目录>/removed`。"""
    return Path(project_root) / REMOVED_DIRNAME


@dataclass(slots=True)
class RemovedFile:
    """一个被卸掉（搬走）的文件。"""

    path: Path
    keep: Path
    size: int
    sha256: str

    @property
    def line(self) -> str:
        return f"{self.path}：{self.size:,} B / sha256 {self.sha256[:16]}… → {self.keep}"


@dataclass(slots=True)
class RemoveReport:
    """一次卸载的读数（干跑只填「打算搬什么」，**一个字节都不动**）。"""

    game_root: Path
    project_root: Path
    dry_run: bool
    files: tuple[RemovedFile, ...] = ()
    absent: tuple[Path, ...] = ()
    record_path: Path | None = None

    @property
    def uninstalled(self) -> bool:
        """这次是不是真的把文件搬走了（干跑与「本来就没有」都是 False）。"""
        return not self.dry_run and bool(self.files)

    def lines(self) -> tuple[str, ...]:
        head = "干跑（一个字节都没动）" if self.dry_run else "已卸载"
        out = [f"[外部库·卸载] {head}"]
        out.extend("  " + item.line for item in self.files)
        out.extend(f"  {p}：本来就没有（幂等，不算错）" for p in self.absent)
        if self.record_path is not None:
            out.append(f"  卸载台账：{self.record_path}")
            out.append("  还原 = 按台账把文件搬回原路径（先比对 sha256，再搬）")
        return tuple(out)


def _uninstall_targets(root: Path, extra_files: Sequence[str | Path]) -> list[Path]:
    """这次要卸哪些文件：默认 `<游戏根>/BA_Mod.zip`；`extra_files` 只许在游戏根**里面**。"""
    base = root.resolve()
    targets = [game_zip_path(root)]
    for raw in extra_files:
        candidate = Path(raw)
        full = (candidate if candidate.is_absolute() else base / candidate).resolve()
        if full == base or not full.is_relative_to(base):
            raise AssetError(
                f"要卸的文件不在游戏根里：{full}",
                hint=(
                    f"只许卸游戏根（{base}）里面的东西 —— 传相对路径即可，"
                    "例：BrokenArrow_Data/StreamingAssets/aa/PC/xxx.bundle"
                ),
            )
        targets.append(full)
    return targets


def _keep_path(dest_root: Path, root: Path, path: Path) -> Path:
    """搬到 `removed/` 里的**扁平名**（相对路径的分隔符换成 `__`，免得造一串目录）。"""
    rel = path.resolve().relative_to(root.resolve())
    return dest_root / "__".join(rel.parts)


def _append_removed_record(project: Path, root: Path, moved: Sequence[RemovedFile]) -> Path:
    """把这次卸掉的东西记进 `<工程目录>/removed/removed.json`（同一路径只留最新一条）。"""
    path = removed_root(project) / REMOVED_RECORD
    doc: dict = {"kind": "external-db-removed", "game_root": str(root), "files": []}
    if path.is_file():
        with suppress(OSError, ValueError):
            old = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(old, dict) and isinstance(old.get("files"), list):
                doc = old
                doc["game_root"] = str(root)
    by_path = {str(e.get("path")): e for e in doc["files"] if isinstance(e, dict)}
    for item in moved:
        by_path[str(item.path)] = {
            "path": str(item.path),
            "keep": str(item.keep),
            "bytes": item.size,
            "sha256": item.sha256,
            "removed_at": datetime.now().isoformat(timespec="seconds"),
        }
    doc["files"] = list(by_path.values())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def remove(
    game_root: str | Path,
    *,
    project_root: str | Path,
    extra_files: Sequence[str | Path] = (),
    dry_run: bool = False,
    check_game_running: bool = True,
) -> RemoveReport:
    """把 `<游戏根>/BA_Mod.zip` **卸掉**（模块头第 1 节：删掉那个文件即卸载）＋ 留可还原条目。

    与 :func:`restore` 的区别（⛔ 别混）：`restore` 还原的是「**装之前**那一份」；
    这里要的是「**这个文件不存在**」那个状态。2026-10-12 的现场正是两者不同的例子：
    装之前那份 326,821 B 本身就带 3 行测试单位，拿它当「回原版」是错的。

    `extra_files`
        同一批「回原版」要拿掉、但本通道不产的其他文件（例：一个 catalog 已经不认的孤儿
        bundle）。每个以**游戏根**为基准解析；⛔ 出了游戏根直接拒
        —— 别让它变成「随手删任意文件」的入口。默认空 = 只卸 `BA_Mod.zip` 这一件。
    `dry_run`
        一个字节都不动；但照样跑红线 1（游戏在跑就拒）—— 否则那份「判据一行」
        描述的是一次注定要失败的写。

    写后复核（⛔ 不许省）：搬完逐个核 ① 原位置不见了；② 新位置的 sha256 == 搬之前那个。
    同盘守卫沿用红线 2：搬的目标必须与游戏同盘，否则 `shutil.move` 会退化成「先拷一份再删」
    —— 那一瞬间硬盘上就是两份，正是 ADR 0007 第 4 条要避免的。
    """
    root = Path(game_root)
    project = Path(project_root)
    if check_game_running:
        _check_game_not_running("卸载")
    if not root.is_dir():
        raise AssetError(
            f"游戏根目录不存在：{root}",
            hint="选含 BrokenArrow_Data 的那一层；本模块不会替你把目录造出来。",
        )
    dest_root = removed_root(project)
    files: list[RemovedFile] = []
    absent: list[Path] = []
    for path in _uninstall_targets(root, extra_files):
        if not path.is_file():
            absent.append(path)
            continue
        data = path.read_bytes()
        files.append(
            RemovedFile(
                path=path,
                keep=_keep_path(dest_root, root, path),
                size=len(data),
                sha256=_sha256(data),
            )
        )
    report = RemoveReport(
        game_root=root,
        project_root=project,
        dry_run=dry_run,
        files=tuple(files),
        absent=tuple(absent),
    )
    if dry_run or not files:
        return report

    dest_root.mkdir(parents=True, exist_ok=True)
    assert_same_drive(dest_root, root)
    moved: list[RemovedFile] = []
    for item in files:
        keep = item.keep
        if keep.exists():  # 同一个文件卸第二次：不覆盖上一份，加 sha 前缀区分
            keep = keep.with_name(f"{item.sha256[:8]}-{keep.name}")
        shutil.move(str(item.path), str(keep))
        moved.append(RemovedFile(path=item.path, keep=keep, size=item.size, sha256=item.sha256))
    report.files = tuple(moved)
    report.record_path = _append_removed_record(project, root, moved)

    problems: list[str] = []
    for item in moved:
        if item.path.exists():
            problems.append(f"原位置还在：{item.path}")
        elif not item.keep.is_file():
            problems.append(f"搬过去的文件不见了：{item.keep}")
        elif sha256_of(item.keep) != item.sha256:
            problems.append(f"搬过去的 sha256 对不上：{item.keep}")
    if problems:
        raise AssetError(
            "卸载写后复核不通过：" + "；".join(problems),
            hint="文件已经搬走了，按上面那句逐条核，再按卸载台账搬回去。",
        )
    return report


def read_removed_record(project_root: str | Path) -> dict:
    """读卸载台账；⛔ 没有台账**不是错**（没卸过就是没卸过），返回一份空文档。"""
    path = removed_root(project_root) / REMOVED_RECORD
    if not path.is_file():
        return {"kind": "external-db-removed", "files": []}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AssetError(
            f"卸载台账读不开：{path}（{exc}）",
            hint="台账坏了就不知道每份文件该回哪、sha 是多少 —— 别手工搬，先把它修好或找回来。",
        ) from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), list):
        raise AssetError(
            f"卸载台账格式不对：{path}",
            hint='要一个 {"kind":…,"files":[…]} 的 JSON；每条至少 path/keep/sha256。',
        )
    return doc


@dataclass(slots=True)
class UnremoveReport:
    """一次「搬回去」的读数（干跑只填「打算搬什么」，**一个字节都不动**）。"""

    project_root: Path
    dry_run: bool
    restored: tuple[RemovedFile, ...] = ()
    already: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()
    record_path: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.problems

    def lines(self) -> tuple[str, ...]:
        head = "干跑（一个字节都没动）" if self.dry_run else "已搬回去"
        out = [f"[外部库·还原卸载] {head}"]
        out.extend(f"  {item.path}：{item.size:,} B / sha256 {item.sha256[:16]}… ← {item.keep}"
                   for item in self.restored)
        out.extend(f"  {text}" for text in self.already)
        out.extend(f"  ✘ {text}" for text in self.problems)
        if self.record_path is not None:
            out.append(f"  卸载台账已更新：{self.record_path}")
        return tuple(out)


def _matches(entry: Mapping[str, Any], wanted: Sequence[str]) -> bool:
    """`--file` 只给基名也认（用户手上不一定有完整路径）。"""
    if not wanted:
        return True
    path = os.path.normcase(os.path.abspath(str(entry.get("path", ""))))
    name = os.path.basename(path)
    for raw in wanted:
        text = os.path.normcase(os.path.abspath(str(raw)))
        if text == path or os.path.normcase(str(raw)) == name or os.path.basename(text) == name:
            return True
    return False


def unremove(
    project_root: str | Path,
    *,
    files: Sequence[str | Path] = (),
    dry_run: bool = False,
    check_game_running: bool = True,
) -> UnremoveReport:
    """按卸载台账把文件**搬回原路径**（`remove()` 的逆操作；这是唯一认可的还原口径）。

    ⛔ 与 :func:`restore` 的分工：那个还原的是「**装之前**那一份」（补丁柜）；
    这个还原的是「**被 `remove()` 搬走的那一份**」（卸载台账）。

    两把锁（照 `remove()` 与补丁柜的同一套口径）：
    ① 搬之前先比 **sha256** —— 台账说多少、盘上那份就得是多少，对不上就**拒绝**（不是覆盖）；
    ② 原位置若已经有东西：逐字节相同 ⇒ 报「已经在原位上」并跳过；**不相同 ⇒ 拒绝**，
    绝不覆盖用户后来放进去的东西。

    搬完逐个复核（原位置在不在、sha256 对不对），并把搬回去的条目从台账里划掉。
    """
    project = Path(project_root)
    if check_game_running:
        _check_game_not_running("还原卸载")
    doc = read_removed_record(project)
    record = removed_root(project) / REMOVED_RECORD

    already: list[str] = []
    problems: list[str] = []
    pending: list[tuple[dict, Path, Path, str]] = []
    for raw in doc["files"]:
        if not isinstance(raw, dict) or not _matches(raw, files):
            continue
        dst = Path(str(raw.get("path", "")))
        src = Path(str(raw.get("keep", "")))
        sha = str(raw.get("sha256", ""))
        if not dst.name or not src.name or not sha:
            problems.append(f"台账这一条缺 path/keep/sha256：{raw}")
            continue
        if dst.exists():
            if sha256_of(dst) == sha:
                already.append(f"{dst}：已经在原位上（sha 对得上），跳过")
            else:
                problems.append(f"{dst} 已经有一份**不一样**的东西 —— 拒绝覆盖（先自己处理掉它）")
            continue
        if not src.is_file():
            problems.append(f"台账里那份不见了：{src}")
            continue
        actual = sha256_of(src)
        if actual != sha:
            problems.append(f"{src} 的 sha256 是 {actual[:16]}…，台账说是 {sha[:16]}… —— 拒绝搬")
            continue
        pending.append((raw, src, dst, sha))

    report = UnremoveReport(
        project_root=project,
        dry_run=dry_run,
        restored=tuple(
            RemovedFile(path=dst, keep=src, size=int(raw.get("bytes", 0)), sha256=sha)
            for raw, src, dst, sha in pending
        ),
        already=tuple(already),
        problems=tuple(problems),
    )
    if dry_run or not pending:
        return report

    moved: list[RemovedFile] = []
    for raw, src, dst, sha in pending:
        assert_same_drive(src, dst.parent)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved.append(RemovedFile(path=dst, keep=src, size=int(raw.get("bytes", 0)), sha256=sha))
    report.restored = tuple(moved)

    for item in moved:
        if not item.path.is_file():
            problems.append(f"搬回去了但原位置没有：{item.path}")
        elif sha256_of(item.path) != item.sha256:
            problems.append(f"搬回去的 sha256 对不上：{item.path}")
    report.problems = tuple(problems)

    done = {str(item.keep) for item in moved}
    doc["files"] = [e for e in doc["files"] if str(e.get("keep")) not in done]
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    report.record_path = record
    if problems:
        raise AssetError(
            "还原卸载写后复核不通过：" + "；".join(problems),
            hint="文件已经搬回去了，逐条核上面那句；台账里剩下的那几条还能再搬一次。",
        )
    return report
