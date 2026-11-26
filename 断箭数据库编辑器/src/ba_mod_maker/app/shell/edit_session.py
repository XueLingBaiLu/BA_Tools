"""写盘通道（界面层唯一的一份）：一个实例、一份变更集、一个落点（零业务逻辑）。

## 为什么要有它

"改一格就自动写盘"这个口径（用户定的）意味着**每个能改值的界面都在写同一个包**。
写盘这件事如果各页各写一份，就会出现我们实测过的那个坑：

    BA_Mod.zip 里没改的表是**逐字节从源文件搬过来**的（core/edit/package.py 的
    _original_bytes）。所以"A 页存 X 表、B 页存 Y 表"各写各的时，B 那一次会把
    X 表**按源文件的字节写回去** —— 刚改好的 X 在包里就没了。

判据很简单：**changed_tables 必须是整个会话累计出来的**，不能是某一页自己那几笔。

所以这里只做三件事，全应用共用一份：

1. 拿着当前实例（set_instance）与变更集（Changeset）；
2. save()：挑出要重新序列化的表 -> `core/session.py::save_instance` -> commit
   （成功认账，失败保留改动让用户重试）；
3. 把"存到哪 / 存了没有"这两件事通过信号讲出去（顶栏那个胶囊听它）。

界面**不许**自己拼 plan/write/verify —— 那三句只能出现在 `core/session.py`
（`save_instance`）与 `core/edit/package.py`。

## 落到哪个文件：**写回你打开的那个文件**（用户口径 2026-09-30）

| 打开的是 | 自动保存写到 |
|---|---|
| `BA_Mod.zip`（老名字 `db.zip` 也认） | **那个文件本身**（同一个路径，原子替换） |
| `DataBaseCompiled.json` | **那个文件本身**（按容器对象的文本写，不是包） |
| `DataBaseCompiled/` 目录 | `<该目录的上一级>/BA_Mod.zip`（行为不变） |
| `data.unity3d` | 同级目录的 `BA_Mod.zip`（**不写回 7.1 GB 容器**） |

落点口径只有一份实现：`core/edit/package.py::default_save_target()`（这里只是
多一层环境变量接缝）。

## 一次会话里改了两张表，第二次保存不能把第一张抹掉

未改动的表是从**打开时那份来源**逐字节搬的（不是从上次写的包搬的）。所以只带
"本次改的表"去保存时：改了 A 存一次、再改 B 存一次，后一次会把 A 按**打开时**
的字节写回去 —— 用户第一笔改动静默消失。落点是他自己那个包时这就是数据丢失。
所以 `save()` 每次都带上**上一次已经写进这个落点的表**（见 `_tables_to_write`）。

## 第二条产物：资产改动落 Δ 计划（ADR 0008 决定 4）

一个会话里有两份账本：**表改动**（`Changeset`）与**资产改动**（`AssetChangeset`）。
`save()` 一次冲出两条产物 —— 表 → **包**（上面那一套，路径不变）；资产 → **Δ 计划**
（`<工程目录>/patches/asset-plan.json`，见 `core/edit/asset_plan_io.py`）。
**两条都不碰游戏目录**：安装 / 撤销是显式动作（`assets` / `skin` 那条链）。

所以早退那一行看的是**两个账本都空**；而只改资产时**一个字节的包都不写** ——
表没改，把整个包重新序列化 + 压一遍就是 AGENTS `0.7.1 说的那种白干。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from ba_mod_maker.core.data.dataset import Instance
from ba_mod_maker.core.edit.asset_changeset import AssetChangeset
from ba_mod_maker.core.edit.asset_plan_io import write_plan
from ba_mod_maker.core.edit.asset_refs import AssetRef
from ba_mod_maker.core.edit.changeset import Changeset
from ba_mod_maker.core.edit.package import PACKAGE_NAME, default_save_target
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.errors import AssetError, BaModMakerError
from ba_mod_maker.core.session import save_instance

#: 自动保存落点的**环境变量覆盖**（与 BA_MOD_MAKER_GAME 同一类接缝）。
#: 用例靠它把落点指到临时目录 —— 否则"改完立刻自动保存"会往工作区根写真的包。
SAVE_DIR_ENV = "BA_MOD_MAKER_SAVE_DIR"

#: Δ 计划（第二条产物）的**工程目录**覆盖。与 SAVE_DIR_ENV 同一类接缝：
#: 用例靠它把计划写到临时目录 —— 否则会往工作区根建一个真的 patches\。
PROJECT_ROOT_ENV = "BA_MOD_MAKER_PROJECT_ROOT"


class EditSession(QObject):
    """整个会话共用的一份编辑状态与写盘通道。"""

    #: 写盘成功（参数：落点绝对路径, 字节数）。
    saved = Signal(str, int)
    #: 写盘失败（参数：给用户看的一句说明）。**错误仍然要弹窗**（AGENTS §5）。
    failed = Signal(str)
    #: 内存里的数据变了（界面据此刷新角标 / 计数）。
    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._instance: Instance | None = None
        self._changeset: Changeset | None = None
        self._save_path: Path | None = None
        self._last_saved: Path | None = None
        #: 上一次写进**当前落点**的那几张表。见 `_tables_to_write()`：
        #: 少了它，第二次保存会把第一次改好的表按"打开时"的字节写回去（静默丢改动）。
        self._written: list[str] = []
        #: 资产账本（ADR 0008 决定 3：与表改动**同脏态、同 save**，各一份账本）。
        #: 只在**接上基线**之后才存在 —— 基线是「这一处现在是什么」的唯一来源，
        #: 没接上就写不出一条可信的 §old§（见 AssetChangeset.__init__ 的三条契约）。
        self._assets: AssetChangeset | None = None
        #: 第二条产物的工程目录（Δ 计划落 <工程目录>/patches/）。None = 当前目录。
        self._project_root: Path | None = None

    # ------------------------------------------------------------------ #
    # 实例与变更集
    # ------------------------------------------------------------------ #

    def set_instance(self, instance: Instance | None) -> None:
        """换数据源：变更集跟着重建（上一个实例的条目没有意义了）。"""
        self._instance = instance
        self._changeset = Changeset(instance) if instance is not None else None
        self._last_saved = None
        # 换了实例 = 换了一份数据与来源，上一个落点写过哪些表与这里无关了。
        self._written = []

    @property
    def instance(self) -> Instance | None:
        return self._instance

    @property
    def changeset(self) -> Changeset | None:
        return self._changeset

    @property
    def is_empty(self) -> bool:
        """**两个账本都空**才算「一笔新改动都没有」（ADR 0008 决定 3）。"""
        return self.change_count == 0 and self.asset_change_count == 0

    @property
    def change_count(self) -> int:
        return 0 if self._changeset is None else self._changeset.count

    @property
    def last_saved_path(self) -> Path | None:
        return self._last_saved

    # ------------------------------------------------------------------ #
    # 资产账本（ADR 0008 · 与表账本并列，同一个 save）
    # ------------------------------------------------------------------ #

    def set_asset_baseline(self, baseline: Callable[[AssetRef], Any] | None) -> None:
        """接上「这一处现在是什么」。**换基线 = 换账本**（上一个基线的条目没有意义了）。

        基线是**唯一**来源：真实实现由「打开那个包」的那一层给
        （`AssetChangeset.__init__` 里写死的那三条契约）。传 `None` = 摘掉账本 ——
        此后资产动词会**报错**，而不是记一条 `old` 不可信的改动。
        """
        self._assets = None if baseline is None else AssetChangeset(baseline)

    @property
    def asset_changeset(self) -> AssetChangeset | None:
        return self._assets

    @property
    def asset_change_count(self) -> int:
        """**资产**条目有几条（与 `change_count` 各报各的，设计稿 `4）。"""
        return 0 if self._assets is None else self._assets.count()

    @property
    def total_change_count(self) -> int:
        """顶栏胶囊那个数 = 表条目 + 资产条目（AGENTS `4.5：脏态唯一指示）。"""
        return self.change_count + self.asset_change_count

    def set_asset_field(self, ref: AssetRef, value) -> None:
        """**修改**一个既有对象的字段（原子动词 `set`）。内存里生效，**不落盘**。"""
        self._assets_or_fail().set_field(ref, value)
        self.changed.emit()

    def add_asset_object(self, ref: AssetRef, payload: bytes) -> None:
        """**新增**一个对象（原子动词 `add`）。"""
        self._assets_or_fail().add_object(ref, payload)
        self.changed.emit()

    def delete_asset_object(self, ref: AssetRef) -> None:
        """**删除**一个对象（原子动词 `delete`）。"""
        self._assets_or_fail().delete_object(ref)
        self.changed.emit()

    def revert_assets(self) -> int:
        """丢掉**全部**资产改动，返回还原了几条（与 `revert_all` 各管各的账本）。"""
        if self._assets is None or self._assets.is_empty():
            return 0
        count = self._assets.count()
        self._assets.revert_all()
        self.changed.emit()
        return count

    def _assets_or_fail(self) -> AssetChangeset:
        if self._assets is None:
            raise AssetError(
                "资产账本还没接上基线",
                hint="先 set_asset_baseline(AssetRef -> 现在的值 / MISSING)："
                "old 只在第一次改动时记，而它只能从基线来（ADR 0008 决定 6）",
            )
        return self._assets

    # ------------------------------------------------------------------ #
    # 写
    # ------------------------------------------------------------------ #

    def set_value(self, ref: CellRef, value) -> None:
        """写一格（走变更集，内存里生效）。**不落盘** —— 落盘由界面显式调 save()。"""
        if self._changeset is None:
            return
        self._changeset.set_value(ref.table, ref.row_id, ref.column, value)
        self.changed.emit()

    # ---- 读：账本是**唯一**一份口径（数据表页的黄底与「本表 N 处」都读这里）----

    def is_dirty(self, table: str, row_id: int, column: str) -> bool:
        """这一格有没有未保存的改动。"""
        return self._changeset is not None and self._changeset.is_dirty(table, row_id, column)

    def dirty_count(self, table: str) -> int:
        """**这一张表**有几处未保存的改动（列级 + 行级）。

        顶栏胶囊用的是整份会话的 `change_count`；这个只回答"这一张表" ——
        数据表页那个「放弃改动」按钮的作用域就是本表，它得知道自己管着几处。
        """
        if self._changeset is None:
            return 0
        row_changes = [c for c in self._changeset.row_changes if c.table == table]
        return len(self._changeset.changes_of(table)) + len(row_changes)

    def revert_table(self, table: str) -> int:
        """丢掉**这一张表**的改动，返回还原了几处。

        与 `revert_all()` 同一套做法：逐条把**旧值**写回去，**不**从磁盘重读整表 ——
        重读等于拿磁盘覆盖内存（磁盘上本来就与内存不一致的地方会被一起抹掉），
        而且会把 `Table` 对象换掉，别处持有的引用当场悬空；
        来源是 zip / 容器时磁盘上根本没有散文件可读。
        """
        if self._changeset is None:
            return 0
        column_changes = self._changeset.changes_of(table)
        row_changes = tuple(c for c in self._changeset.row_changes if c.table == table)
        if not column_changes and not row_changes:
            return 0
        # 顺序：**先还原列，再撤行** —— 与 Changeset.revert_all 同一个理由：
        # 往一行写旧值可能落在一个已经被拿掉的行上。
        for change in reversed(column_changes):
            self._changeset.revert(change)
        for row_change in reversed(row_changes):
            self._changeset.revert_row(row_change)
        self.changed.emit()
        return len(column_changes) + len(row_changes)

    def revert_all(self) -> int:
        """把所有未保存的改动还原掉（**两个账本都算**），返回还原了几处。"""
        assets = 0
        if self._assets is not None and not self._assets.is_empty():
            assets = self._assets.count()
            self._assets.revert_all()
        tables = 0
        if self._changeset is not None and not self._changeset.is_empty:
            tables = self._changeset.count
            self._changeset.revert_all()
        if assets or tables:
            self.changed.emit()
        return tables + assets

    def save(self, *, silent: bool = False) -> str:
        """写盘。返回一句给状态栏的话（**成功与"没有改动"都不弹窗**）。

        silent=True 是自动保存那条路（改完立刻写）：没有改动时不吭声，
        免得每点一格都在状态栏刷一句"没有改动"。

        失败时**不吞**：发 failed 信号（顶栏胶囊变红、界面弹窗），返回那句说明。

        **落到哪个文件由 `core/session.py::save_instance` 决定**：打开的是文件就写回
        那个文件（zip 写包、json 写文本），目录来源与 data.unity3d 仍写同级目录的
        `BA_Mod.zip`。所以那句话里带的是**完整路径** —— 用户要能看出这一下存到哪儿了。
        """
        if self._instance is None or self._changeset is None:
            return "" if silent else "尚未加载数据源"
        # ❗ **账本空了 = 一笔新改动都没有 -> 一次盘都不写**（2026-10-01 实测）。
        #
        #    为什么必须挡在这一句上：`_tables_to_write()` 会把 `_written`（上次写进这个
        #    落点的那几张表）一起带出来，于是"改完存一次、**没有新改动**再点一次 💾"
        #    会把整个包重新序列化 + 重新压一遍 —— 盘上那份已经是最新的，这一下纯白干。
        #    实测（_work/probe_save_cost.py，目录来源，改的是 WeaponAmmunitions + Units）：
        #      · 有改动一次保存 wall 56.2 ms（zip 压缩 42.8 / 复核 7.1 / plan 5.4）
        #      · 没有新改动再点一次 wall 57.6 ms（zip 压缩 42.2）<- 就是被这句挡掉的
        #      · 全新会话零改动 0.002 ms（下面这条路本来就早退）
        #
        #    ⚠️ 保存**口径**一个字没动（AGENTS §4.5）：落点、临时文件 + 原子替换、
        #    "先复核后替换"、`_verify_dbzip_written` 全都不碰。这条只是把"没有改动就别写盘"
        #    那句判据从"`_tables_to_write()` 非空"挪到"账本里有没有**新**改动" ——
        #    后者才是用户点那颗按钮时的真实语义。
        tables_dirty = not self._changeset.is_empty
        assets_dirty = self._assets is not None and not self._assets.is_empty()
        # ❗ 口径从「表账本空」扩到「**两个账本都空**」（ADR 0008 决定 3）。
        #    上面那段「55 ms 白干」的分析一个字没变，只是判据放宽了一格。
        if not tables_dirty and not assets_dirty:
            return "" if silent else "没有改动，未写盘"
        if not tables_dirty:
            # 只有资产改动：⛔ **不许**顺手把整个包重写一遍（表一个字节都没改）。
            return self._save_assets_only(silent=silent)
        changed = self._tables_to_write()
        if not changed:  # 到不了：is_empty 为假 -> fresh 非空。留着当保险（口径只有一处）
            return "" if silent else "没有改动，未写盘"
        target = Path(self._save_path or self.default_save_path())
        try:
            report = save_instance(self._instance, changed, target=target)
        except (BaModMakerError, OSError) as exc:
            message = str(exc)
            self.failed.emit(message)
            return message
        # 落盘成功 -> 把这批改动**认下来**（见 core/edit/changeset.py::commit 的说明），
        # 并记住"这几张表已经写进这个落点了"：下一次保存必须连着它们一起重新序列化。
        self._changeset.commit()
        self._written = list(changed)
        self._last_saved = Path(report.path)
        self.saved.emit(str(report.path), report.total_bytes)
        notes = [
            f"已写回 {report.path}（{len(changed)} 张表改动，{report.total_bytes:,} 字节）"
        ]
        if self._assets is not None and not self._assets.is_empty():
            # 一次 save 冲出**两条产物**（ADR 0008 决定 4）：包已经写成功了，
            # 计划这一半若失败要**如实报出来** —— ⛔ 不许假装整次保存都成功。
            problem, note = self._write_asset_plan()
            if problem is not None:
                self.failed.emit(problem)
                return problem
            notes.append(note)
        sentence = "；".join(notes)
        if silent:
            return f"已自动保存：{sentence}"
        return sentence

    def _tables_to_write(self) -> tuple[str, ...]:
        """这次保存**必须重新序列化**的表 = 本次改的 + 上一次已经写进这个落点的。

        为什么后半截不能省（会**静默丢用户的改动**）：未改动的表不是从"上次写的包"
        搬的，而是从**打开时那份来源**逐字节搬的（见 core/edit/package.py）。只带本次
        改的表时，"改第二张表"那一次保存会把第一张表按**打开时**的字节写回去 ——
        用户第一次那笔改动就此消失，而且没有任何提示。落点是他自己那个包时，
        这就是数据丢失。

        ❗ **它只在"有新改动"时才被调用**（`save()` 先看账本空不空，见那里的说明）：
        `_written` 是历史，不是"这次要写什么"—— 账本为空时把历史再写一遍就是白干 55ms。
        为什么这样安全：`commit()` 之后盘上那份 == 内存里那份，此后一笔不改就没有
        任何"盘上没有的新值"；而界面上的撤销 / 放弃改动走的是 `set_value(旧值)`，
        那会在账本里留下一条新记录（`old` 是上次 commit 的那份），账本不为空，
        照样会写。
        """
        fresh = list(self._changeset.changed_tables()) if self._changeset is not None else []
        return tuple([*fresh, *(t for t in self._written if t not in fresh)])

    def default_save_path(self) -> Path:
        """自动保存写到哪儿：**打开的是文件就写回它本身**（用户口径 2026-09-30）。

        优先级：

        1. `BA_MOD_MAKER_SAVE_DIR`（测试 / 调试接缝：指到哪就写到哪；用例靠它别往
           真实目录写包）；
        2. `core/edit/package.py::default_save_target(instance)` —— 落点口径的
           **唯一实现**：`BA_Mod.zip` / `DataBaseCompiled.json` 写回**那个文件本身**；
           目录数据源与 `data.unity3d` 仍写 `<数据源同级>/BA_Mod.zip`。

        为什么不再固定写"数据源同级目录"：用户报的就是这个 —— 他打开
        `测试文件\\BA_Mod.zip`，自动保存却落到工作区根的 `BA_Mod.zip`，**不是他打开的那个文件**。

        为什么不弹文件对话框问：用户口径是"改了后自动保存"，每改一格弹一次选择框
        比不保存还烦。
        """
        override = os.environ.get(SAVE_DIR_ENV, "").strip()
        if override:
            return Path(override) / PACKAGE_NAME
        if self._instance is None:
            return Path.cwd() / PACKAGE_NAME
        return default_save_target(self._instance)

    def set_save_path(self, path) -> None:
        """测试与将来「另存为」用：指定保存落点，不再弹选择框。

        换了落点 = 换了一份要在上面累积改动的包，所以"已经写进去过的那几张表"清零
        （见 `_tables_to_write`）。
        """
        self._save_path = Path(path) if path is not None else None
        self._written = []


    # ------------------------------------------------------------------ #
    # 第二条产物：资产 Δ 计划
    # ------------------------------------------------------------------ #

    def _save_assets_only(self, *, silent: bool) -> str:
        """只有资产改动时的保存：**不碰包**，只写 Δ 计划。"""
        problem, note = self._write_asset_plan()
        if problem is not None:
            self.failed.emit(problem)
            return problem
        return f"已自动保存：{note}" if silent else note

    def _write_asset_plan(self) -> tuple[str | None, str]:
        """写出**第二条产物**：资产 Δ 计划（`<工程目录>/patches/asset-plan.json`）。

        返回 `(报错话术, 成功话术)` —— 前者不为 `None` 时由调用方发 `failed` 信号
        （AGENTS `5：错误必须看得见，⛔ 不许吞）。

        成功即 `commit()`：这批改动**认下来**，再点一次保存不会写第二遍（AGENTS `4.5）。
        """
        if self._assets is None or self._assets.is_empty():
            return None, ""
        plan = self._assets.plan()
        try:
            path = write_plan(plan, project_root=self.default_project_root())
            size = path.stat().st_size
        except (BaModMakerError, OSError) as exc:
            return str(exc), ""
        self._assets.commit()
        self.saved.emit(str(path), size)
        return None, f"已写出资产 Δ 计划 {path}（{plan.count} 条资产改动）"

    # ------------------------------------------------------------------ #
    # 工程目录（第二条产物的落点）
    # ------------------------------------------------------------------ #

    def set_project_root(self, path) -> None:
        """指定工程目录（Δ 计划落 `<工程目录>/patches/`）。`None` = 当前目录。"""
        self._project_root = Path(path) if path is not None else None

    def default_project_root(self) -> Path:
        """Δ 计划写到哪个工程目录。

        优先级（照 `default_save_path` 的同一套接缝）：

        1. `BA_MOD_MAKER_PROJECT_ROOT`（测试 / 调试接缝：指到哪就写到哪）；
        2. `set_project_root()` 给的那个；
        3. 当前目录 —— 与 `cli/__main__.py::_project_root` 的「没给就当前目录」同一口径。
        """
        override = os.environ.get(PROJECT_ROOT_ENV, "").strip()
        if override:
            return Path(override)
        return self._project_root or Path.cwd()


__all__ = ["PROJECT_ROOT_ENV", "SAVE_DIR_ENV", "EditSession"]
