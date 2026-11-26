"""数据库的**打开 / 保存 / 提取 / 导入**（零 Qt）。

这是把 core/asset/ 的四个模块接成"用户能用的四个动作"的那一层。
界面只跟这里打交道，不直接碰字节。

## 三样东西的关系（用户口径，实测钉死）

    data.unity3d  ⊃  DataBaseCompiled 对象  ==  DataBaseCompiled.json  --解密-->  BA_Mod.zip

| 形式 | 内容 | 大小 |
|---|---|---|
| data.unity3d | UnityFS 容器，数据库是其中的一个对象 | 6.6 GiB |
| DataBaseCompiled.json | 那个对象的 JSON 序列化（头 / 24 个加密字段 / 尾） | ~5.8 MB |
| BA_Mod.zip | 24 张表的**明文** JSON —— 我们**产出**的就是这个包 | ~320 KB |

> 📛 **命名口径**：我们**产出 / 导出**的包叫 **`PACKAGE_NAME` = `BA_Mod.zip`**；
> 放进游戏根目录的那一份（补丁的外部库落点）也叫 `BA_Mod.zip`。
> **唯一还叫 `db.zip` 的是游戏原生那条路** —— `Scenarios\\<场景>\\db.zip`，
> 那是游戏自己的机制（工坊 mod 也用它），不归我们的补丁管。
> 打开文件时**两种名字都认**：识别按内容（PK 头），不看文件名。

## 四个动作

| 动作 | 语义 |
|---|---|
| **打开** | 自动识别三种文件，统一解成 ObjectMeta；保存写回**原文件** |
| **保存** | 写回打开时的那个文件：BA_Mod.zip 重新打包；json 重新序列化；容器变长重写 + 原子替换 |
| **提取** | 从 data.unity3d 或 DataBaseCompiled.json 导出 BA_Mod.zip |
| **导入** | 把 DataBaseCompiled.json 或 BA_Mod.zip **写进** data.unity3d |

## 两个“保存”入口（别混）

| 入口 | 手上是什么 | 谁用 |
|---|---|---|
| `save` | `OpenedSource`（字节） | CLI / 界面「保存」菜单（写回容器那条 4 分钟的路） |
| `save_instance` | `Instance`（表行） | GUI「改一格就自动保存」（`app/shell/edit_session.py`） |

两条的**落点口径是同一条**（“写回打开时的那个文件”），但 `save_instance` 还要按来源的
**格式**写（zip 写平铺条目 + 未知条目；json 写那个容器对象的文本），并且只认
`Instance.opened_file` —— `data.unity3d` 没有它：7.1 GB 的重写不能挂在自动保存上。

## 两条实测出来的约束

1. **写回 data.unity3d 必须游戏已关**。实测占着文件时读没问题（0.12 秒读出对象头），
   但覆盖会以 WinError 5 失败。
2. **加密需要一个模板**。BA_Mod.zip 只有明文，没有对象头/尾、pathID、IV ——
   所以从 BA_Mod.zip 导入时必须拿一份 data.unity3d 或 DataBaseCompiled.json 当模板。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ba_mod_maker.core.asset import container, dbzip, ufs
from ba_mod_maker.core.asset.tables import TABLES
from ba_mod_maker.core.data.dataset import FORMAT_JSON, Instance, OpenedFile
from ba_mod_maker.core.errors import AssetError, DataSourceError, UserFacingError

#: **我们产出的那个包的名字**：导出默认名、界面文案、CLI 帮助都用它。
#: 它同时也是**游戏根目录里外部库的落点名字**（补丁认这个名字，见 `core/asset/dllpatch.py`）。
#: ⚠️ 唯一还叫 `db.zip` 的是游戏原生那条路（`Scenarios\<场景>\db.zip`），不归补丁管。
PACKAGE_NAME = "BA_Mod.zip"

#: 外部库在游戏目录里的**子文件夹**（2026-10-27 用户口径：mod 文件统一收进 `BA_Mod\`）。
#: 资产包（`*.bundle`）本来就落在这里，数据库也跟着进来，不再散落在游戏根。
PACKAGE_DIR = "BA_Mod"

#: DLL 补丁认的**相对路径**（相对进程当前目录 = 游戏根）：`BA_Mod\BA_Mod.zip`。
#: 补丁里现造的就是这一串（见 `core/asset/dllpatch.py::build_cave`）——
#: ⛔ 它和 `game_zip_path()` 必须同源，两边各写一个字面量的症状是「包装好了、游戏还是编译库」。
PACKAGE_REL_PATH = PACKAGE_DIR + "\\" + PACKAGE_NAME


class SourceKind(StrEnum):
    """打开的是哪一种文件。**保存写回原文件，所以这个必须记住。**"""

    UNITY3D = "data.unity3d"
    DBC_JSON = "DataBaseCompiled.json"
    #: 外部库：一个装着 24 张表明文的 zip。**按内容认**（`PK\x03\x04` 头），不看文件名 ——
    #: 我们的成品叫 `PACKAGE_NAME`，用户手上可能还留着改名前的老包（`db.zip`），两种都认。
    DB_ZIP = PACKAGE_NAME

    @property
    def label(self) -> str:
        return {
            SourceKind.UNITY3D: "游戏容器 data.unity3d",
            SourceKind.DBC_JSON: "DataBaseCompiled.json",
            SourceKind.DB_ZIP: f"外部库 {PACKAGE_NAME}",
        }[self]


def detect_kind(path: str | Path) -> SourceKind:
    """按**内容**判断是三种里的哪一种（不看扩展名）。

    - UnityFS 开头 → 容器；
    - PK\x03\x04 开头 → 外部库 zip（`BA_Mod.zip`；改名前的老包 `db.zip` 也走这条）；
    - 以 { 开头 → DataBaseCompiled.json。

    ⚠️ **只看内容，不看文件名** —— 用户手上可能两种名字的文件都有。
    """
    path = Path(path)
    if not path.is_file():
        raise DataSourceError(
            f"找不到文件：{path}",
            hint="确认路径；data.unity3d 在 <游戏根>/BrokenArrow_Data/ 下",
        )
    with path.open("rb") as fh:
        head = fh.read(64)
    if head.startswith(b"UnityFS"):
        return SourceKind.UNITY3D
    if head.startswith(b"PK\x03\x04"):
        return SourceKind.DB_ZIP
    if head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{"):
        return SourceKind.DBC_JSON
    raise DataSourceError(
        f"认不出这是什么文件：{path.name}",
        hint=(
            "只认三种：UnityFS 容器（data.unity3d）、DataBaseCompiled.json、"
            f"外部库 zip（{PACKAGE_NAME}）"
        ),
        head=head[:16].hex(),
    )


def _file_sha256(path: Path) -> str:
    """整个文件的 sha256（闸 1 用）。6.6 GB 实测 5.3 秒 —— 所以它是**惰性**的：
    打开时不算，第一次真的要走闸 1（= 保存）才算，算完缓存在 `OpenedSource` 上。
    """
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 24), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_stamp(path: Path) -> tuple[int, int]:
    """文件的**廉价指纹** `(大小, mtime_ns)`：一次 stat，不读内容（6.6 GB 也一样快）。

    它只有一个用途 —— 给**惰性**的闸 1 哈希当锚点：哈希第一次要的时候才算，
    那一刻它已经错过了「打开时」这个基准；而指纹是**打开时**记下的，
    用来回答「盘上还是我打开时那个文件吗」（见 `OpenedSource.verify_unchanged`）。
    """
    info = path.stat()
    return (info.st_size, info.st_mtime_ns)


@dataclass(slots=True)
class OpenedSource:
    """一次"打开"的结果。

    meta 是**可编辑的数据本体**；reader 只在来源是容器时存在，
    因为写回容器要用它拿块表与节点表。
    """

    kind: SourceKind
    path: Path
    meta: container.ObjectMeta
    #: 来源是容器时：已打开的读取器（写回要用；调用方负责 close）。
    reader: ufs.UfsReader | None = None
    #: 来源是外部库 zip 时：原文件里的未知条目（保存时原样写回）。
    extra_entries: dict[str, bytes] | None = None
    #: 闸 1 的基准哈希：**整个文件**的 sha256（注意它与 meta.source_sha256 不同 ——
    #: 后者是数据库**对象**的哈希）。
    #:
    #: **惰性**：打开时不算（6.6 GB / 实测 5.3 秒），第一次真的要走闸 1（= 保存）
    #: 才由 `verify_unchanged()` 算出来留在这里；之后每次保存复用它，不再重算。
    file_sha256: str = ""
    #: 打开时那个文件的**廉价指纹** `(大小, mtime_ns)`（只有容器来源记它）。
    #:
    #: 为什么必须有它：基准哈希是惰性的，**第一次保存那一刻**才算；若拿它去比
    #: 「刚算的当前哈希」，两边恒等、判据恒真（等于没校验 —— 这个坑本仓踩过一次，
    #: 见 `_verify_rewritten`）。指纹在**打开时**记下，第一次走闸时先比它：
    #: 文件在这中间被动过就当场报错，而不是拿被改过的文件当基准。
    opened_stamp: tuple[int, int] | None = None

    def table(self, name: str) -> container.TableSlot:
        for slot in self.meta.tables:
            if slot.table == name:
                return slot
        raise KeyError(f"这份数据里没有表 {name}")

    def verify_unchanged(self) -> None:
        """闸 1：写前确认盘上还是**打开时**那个文件。不通过就抛 `UserFacingError`。

        两个分支：

        1. 已经有基准哈希（算过 / 调用方给的）-> 重新算一遍当前哈希比对 —— **真比对**；
        2. 还没有 -> 先比打开时记下的廉价指纹，过了才把这次算出来的哈希留作基准：
           **一次保存只算一遍**，而且不是「自己跟自己比」（见 `opened_stamp` 的说明）。
        """
        if self.file_sha256:
            got = _file_sha256(self.path)
            if got != self.file_sha256:
                raise UserFacingError(
                    "文件在我打开之后被改过（sha256 不符）",
                    hint="可能是 Steam 校验、别的工具、或游戏更新。建议重新打开一次再改。",
                    expected=self.file_sha256[:16],
                    got=got[:16],
                )
            return
        if self.opened_stamp is not None and _file_stamp(self.path) != self.opened_stamp:
            raise UserFacingError(
                "文件在我打开之后被改过（大小 / 修改时间变了）",
                hint="可能是 Steam 校验、别的工具、或游戏更新。建议重新打开一次再改。",
            )
        self.file_sha256 = _file_sha256(self.path)

    def close(self) -> None:
        if self.reader is not None:
            self.reader.close()
            self.reader = None

    def __enter__(self) -> OpenedSource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _label_slots(meta: container.ObjectMeta, names: list[tuple[str, str, str]]) -> None:
    """把字段名 / 表名 / 条目名填回 TableSlot。

    容器里只有字段顺序，没有名字 —— 名字来自 core.asset.tables 的权威清单。
    两边数量必须相等，不等就是"表结构变了"，必须报错而不是凑合。
    """
    if len(meta.tables) != len(names):
        raise DataSourceError(
            f"表数量不符：数据里有 {len(meta.tables)} 个字段，权威清单是 {len(names)}",
            hint="游戏更新改过表结构；需要先复核 core/asset/tables.py",
        )
    for slot, (field, table, entry) in zip(meta.tables, names, strict=True):
        slot.field, slot.table, slot.zip_entry = field, table, entry


_NAMES = [(t.field, t.table, t.zip_entry) for t in TABLES]


def open_source(path: str | Path) -> OpenedSource:
    """打开三种文件里的任意一种。

    给的是**游戏根目录**时自动拼 BrokenArrow_Data/data.unity3d（用户口径）。
    """
    p = Path(path)
    if p.is_dir():
        candidate = p / "BrokenArrow_Data" / "data.unity3d"
        if not candidate.is_file():
            raise DataSourceError(
                f"{p} 里没有 BrokenArrow_Data/data.unity3d",
                hint="选游戏根目录（含 BrokenArrow.exe 的那一层）",
            )
        p = candidate

    kind = detect_kind(p)
    if kind is SourceKind.UNITY3D:
        return _open_unity3d(p)
    if kind is SourceKind.DBC_JSON:
        meta = container.ObjectMeta.from_json_text(p.read_text(encoding="utf-8"))
        _label_slots(meta, _NAMES)
        return OpenedSource(kind=kind, path=p, meta=meta)
    z = dbzip.DbZip.read(p)
    return OpenedSource(
        kind=kind,
        path=p,
        meta=_meta_from_dbzip(z),
        extra_entries=z.extra,
    )


def _open_unity3d(path: Path) -> OpenedSource:
    reader = ufs.UfsReader(path)
    try:
        found = ufs.find_database_objects(reader)
        # 用户拍板：**取 pathID 最大的那个**。另一个对象原样不动。
        path_id, blob = found[-1]
        meta = container.ObjectMeta.parse(blob, path_id=path_id)
        _label_slots(meta, _NAMES)
        # ❗ 闸 1 的基准哈希**不在这里算**（6.6 GB 实测 5.3 秒，而「打开看一眼」
        #    / preview_import / CLI info 这些路根本不写盘 —— 那笔钱是白付的）。
        #    只留一份廉价指纹；第一次真的要走闸时才由 verify_unchanged() 算哈希
        #    （见 OpenedSource.opened_stamp：没有它，闸 1 会退化成自己跟自己比）。
        stamp = _file_stamp(path)
    except Exception:
        reader.close()
        raise
    return OpenedSource(
        kind=SourceKind.UNITY3D,
        path=path,
        meta=meta,
        reader=reader,
        opened_stamp=stamp,
    )


def _meta_from_dbzip(z: dbzip.DbZip) -> container.ObjectMeta:
    """外部库 zip 只有明文，**没有**对象头/尾/IV。造一个"匿名模板"。

    ⚠️ 这个模板**用不了**：缺 pathID 与真实的头/尾，写进容器会产出坏文件。
    要写回容器，必须走 import_into_container 并给一份真实模板。
    """
    slots = []
    for naming in TABLES:
        plain = z.tables.get(naming.table)
        if plain is None:
            raise DataSourceError(
                f"这个外部库里没有 {naming.zip_entry}",
                hint=f"缺表的 {PACKAGE_NAME} 是残废的：游戏对缺失的表是【不加载】而不是保留旧值",
            )
        slots.append(
            container.TableSlot(
                field=naming.field,
                table=naming.table,
                zip_entry=naming.zip_entry,
                data=plain,
                iv=b"\x00" * 16,
                pad=0,
            )
        )
    return container.ObjectMeta(
        object_head=b"",
        object_tail=b"",
        tables=slots,
        path_id=None,
        source_sha256="",
    )


def save(
    src: OpenedSource,
    *,
    progress: Callable | None = None,
    token: object | None = None,
    check_sha256: bool = True,
    check_game_running: bool = True,
    verify_after: bool = True,
) -> str:
    """把编辑结果写回**打开时的那个文件**。返回一句人话的结果摘要。

    三道闸（都可关，默认全开）**只对 data.unity3d 生效** ——
    另外两种是小文件、随时可重新生成，挂闸没有意义。
    """
    if src.kind is SourceKind.DB_ZIP:
        return _save_dbzip(src)
    if src.kind is SourceKind.DBC_JSON:
        return _save_dbc_json(src)
    return _save_unity3d(
        src,
        progress=progress,
        token=token,
        check_sha256=check_sha256,
        check_game_running=check_game_running,
        verify_after=verify_after,
    )


@dataclass(frozen=True, slots=True)
class SaveReport:
    """一次“把实例写盘”的结果（给状态栏 / 胶囊用的那两样）。"""

    #: 落到哪个文件（绝对路径）。
    path: Path
    #: 这次真正写进去多少字节。
    total_bytes: int


def save_instance(
    instance: Instance,
    changed_tables: Sequence[str],
    *,
    target: str | Path | None = None,
    stamps: Mapping[str, tuple[int, int, int, int, int, int]] | None = None,
) -> SaveReport:
    """把一个**编辑中的实例**写盘 —— GUI「改一格就自动保存」那条路的唯一入口。

    与 :func:`save` 的分工：那个面向 `OpenedSource`（CLI：手上是字节），
    这个面向 `Instance`（GUI：手上是表行）。落点口径**只有一条**：写回打开时的那个
    文件（用户口径：「保存写回**你打开的那个文件**」）。

    参数
    ----
    target
        省略 = 写回 `Instance.opened_file`（并**按来源的格式**写）；
        给了别的路径（「另存为」/ 目录数据源的默认落点 / 用例的临时目录）=
        写一个 `BA_Mod.zip` 包，**不碰原文件**。
        给的路径恰好就是打开的那个文件时，按来源格式写回（判据是路径相等，不是猜）。

    stamps
        zip 落点的成员时间戳（条目名 -> DOS 6 元组），省略 = `None` = **行为一个字节都不变**
        （时间戳取"现在"）。要"写出来的包与源包逐字节相同"必须给 ——
        唯一来源是 `core/edit/package.py::member_stamps(source)`，
        `cli/row_restore.py` 就是这么用的（那里写着为什么非它不可）。

    ⚠️ **不生成任何 `.bak` / 副本**（用户口径：它凭空占空间，用户自己会备份）：
    走的是“临时文件 -> 读回来复核 -> 原子替换”，成功后临时文件已被用掉。
    """
    from ba_mod_maker.core.edit.package import plan_package, verify_package, write_package

    opened = instance.opened_file
    path = Path(target) if target is not None else (Path(opened.path) if opened else None)
    if path is None:
        raise UserFacingError(
            "没有落点：这个实例不是从文件打开的，也没说存到哪",
            hint="目录数据源与 data.unity3d 的落点由界面层算（core/edit/package.py 的 "
            "default_save_target）；容器那条 4 分钟的路请用「保存」菜单。",
        )

    if opened is not None and _same_path(path, opened.path):
        return _write_back_opened_file(instance, opened, changed_tables, stamps)

    plan = plan_package(instance, changed_tables=changed_tables, stamps=stamps)
    write_package(plan, path)
    problems = verify_package(plan, path)
    if problems:
        raise UserFacingError(
            "写后复核不通过：" + "；".join(problems),
            hint="落点不是用户原来那一份数据，原文件没有被弄坏；请重试。",
        )
    return SaveReport(path=path, total_bytes=plan.total_bytes)


def _write_back_opened_file(
    instance: Instance,
    opened: OpenedFile,
    changed_tables: Sequence[str],
    stamps: Mapping[str, tuple[int, int, int, int, int, int]] | None = None,
) -> SaveReport:
    """按**打开时的格式**把编辑结果写回那个文件。两种格式见 `OpenedFile.format`。

    `stamps` 只对 zip 那一支有意义（json 那一支写的是容器对象的文本，没有成员时间戳）。
    """
    if opened.format == FORMAT_JSON:
        return _write_back_json(instance, opened, changed_tables)

    from ba_mod_maker.core.edit.package import plan_package, replace_package

    # zip：平铺 24 个条目 + 原样带回未知条目（`plan_package` 的默认行为，见它的说明）。
    # 用 replace_package 而不是 write_package：**先复核、后替换** ——
    # 复核不通过时用户那个包一个字节都没动。
    plan = plan_package(instance, changed_tables=changed_tables, stamps=stamps)
    replace_package(plan, opened.path)
    return SaveReport(path=Path(opened.path), total_bytes=plan.total_bytes)


def _write_back_json(
    instance: Instance, opened: OpenedFile, changed_tables: Sequence[str]
) -> SaveReport:
    """把改过的表写进那个 `DataBaseCompiled.json`，原子替换。

    为什么不能像 zip 那样直接写：json 是**容器对象**的序列化，它带着对象头/尾与
    每张表的 IV（见 `core/asset/container.py`）。所以写回要用**打开时那个对象**
    （`OpenedFile.meta`）：改过的表按新行重新序列化进去，没改的表连字节都没被碰过
    （`TableSlot.set_payload` 那个“脏”标志就是干这个的）。
    """
    from ba_mod_maker.core.edit.package import rows_as_dicts

    meta = opened.meta
    if meta is None:
        raise UserFacingError(
            "这个 json 来源缺少容器对象，写不回去",
            hint="正常打开 DataBaseCompiled.json 时一定带着它；出现这句说明来源被手工拼过。",
        )
    by_table = {slot.table: slot for slot in meta.tables}
    for name in changed_tables:
        slot = by_table.get(name)
        if slot is None:
            raise DataSourceError(
                f"打开时那份 json 里没有 {name} 这张表",
                hint="表名按 core/asset/tables.py 的权威清单对齐；对不上说明来源不完整",
                table=name,
            )
        slot.set_payload(rows_as_dicts(instance.table(name)))

    target = Path(opened.path)
    _write_json_text(target, meta)
    return SaveReport(path=target, total_bytes=target.stat().st_size)


def _write_json_text(path: Path, meta: container.ObjectMeta) -> None:
    """把容器对象写成 `DataBaseCompiled.json`：临时文件 -> 读回来复核 -> 原子替换。

    两个"保存"入口走的是**同一个**写盘实现（`:func:`save` 那条与
    `:func:`save_instance` 那条），免得"菜单存的 json"和"自动保存的 json"
    在某个分支上分叉。临时文件失败时一定清掉（它长得像备份）。
    """
    text = meta.to_json_text()
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        # 写完**读回来核一遍**（AGENTS §5：写完真实文件一定复核）。
        # 核不过就不替换 —— 用户手上那份是唯一的。
        if tmp.read_text(encoding="utf-8") != text:
            raise UserFacingError(
                "写后复核不通过：刚写的 json 与预期不一致",
                hint="原文件未动（还是你原来那一份）。请重试。",
            )
        os.replace(tmp, path)
    except BaseException:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def _same_path(a: Path, b: Path) -> bool:
    """两个路径是不是同一个文件。Windows 上大小写不敏感，所以用 normcase 比。"""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _save_dbzip(src: OpenedSource) -> str:
    """写回那个外部库 zip：平铺 24 张表 + **原样带回未知条目**，原子替换。

    ⚠️ 顺序是“**先写临时文件并读回来复核，再替换**”，不是“替换完再复核”：
    这里写的是**用户自己那个包**，复核不过时它必须一个字节都没动。
    失败路径上临时文件一定被删掉 —— 它长得像备份，留着既占地方又会被误当备份。
    """
    z = dbzip.DbZip(
        tables={s.table: s.data for s in src.meta.tables},
        extra=dict(src.extra_entries or {}),
    )
    tmp = src.path.with_suffix(src.path.suffix + ".tmp")
    try:
        z.write(tmp)
        _verify_dbzip_written(tmp, z)
        os.replace(tmp, src.path)
    except BaseException:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    kept = len(z.extra)
    tail = f"，原样保留 {kept} 个未知条目" if kept else ""
    return f"已写回 {src.path}（24 张表{tail}）"


def _verify_dbzip_written(tmp: Path, expected: dbzip.DbZip) -> None:
    """写完**读回来逐条比**（AGENTS §5）。比的是内容和未知条目，不是文件大小。"""
    got = dbzip.DbZip.read(tmp)
    bad = [table for table, blob in expected.tables.items() if got.tables.get(table) != blob]
    if bad or got.extra != expected.extra:
        raise UserFacingError(
            "写后复核不通过：刚写的包里内容与预期不一致",
            hint="原文件未动（还是你原来那一份）。请重试；反复出现就把日志一起报上来。",
            tables=bad[:6],
        )


def _save_dbc_json(src: OpenedSource) -> str:
    """写回那个 `DataBaseCompiled.json`（“先复核、后替换”在 `_write_json_text` 里）。"""
    _write_json_text(src.path, src.meta)
    return f"已写回 {src.path}（{src.path.stat().st_size:,} 字节）"


def _save_unity3d(
    src: OpenedSource,
    *,
    progress: Callable | None,
    token: object | None,
    check_sha256: bool,
    check_game_running: bool,
    verify_after: bool,
) -> str:
    if src.reader is None:
        raise AssetError("这份来源没有打开的容器读取器，无法写回容器")
    from ba_mod_maker.core.assets import game as game_mod

    # 闸 2：检测游戏进程（**写前**）
    if check_game_running and game_mod.is_game_running():
        raise UserFacingError(
            "游戏正在运行，无法写回 data.unity3d",
            hint="实测游戏占着这个文件时覆盖会失败（拒绝访问）。请先退出游戏再保存。",
        )

    reader = src.reader
    # 闸 1：写前核对 sha256 —— 确认盘上还是我打开时那个文件。
    # ⚠️ **惰性**：打开 data.unity3d 时不再无条件算整个文件的 sha256（6.6 GB / 实测
    #    5.3 秒）；第一次真的要走这道闸（= 保存）才算，算完缓存进 OpenedSource。
    #    实现在 `OpenedSource.verify_unchanged()` —— 那里也写着为什么必须有
    #    `opened_stamp`（否则基准哈希与当前哈希是同一时刻算的，判据恒真）。
    if check_sha256:
        src.verify_unchanged()

    found = ufs.find_database_objects(reader)
    path_id, old_blob = found[-1]
    if src.meta.path_id is not None and path_id != src.meta.path_id:
        raise UserFacingError(
            f"容器里的数据库换成了 pathID {path_id}（打开时是 {src.meta.path_id}）",
            hint="游戏可能更新过。建议重新打开。",
        )
    new_blob = bytes(src.meta.to_bytes())
    if new_blob == old_blob:
        return "没有改动，未写盘"

    node = reader.node("globalgamemanagers.assets")
    info = ufs.read_serialized_file(reader, node)
    entry = next(o for o in info.objects if o[1] == path_id)
    abs_start = node.offset + info.data_offset + entry[2]

    tmp = reader.path.with_suffix(reader.path.suffix + ".new")
    # ⚠️ 为什么整个写回过程要包在 try 里：tmp 是一个**满尺寸**的中间文件（约 7.1 GB）。
    #    写后复核不通过、或替换被拒（WinError 5）时若不删它，它就会留在游戏目录里：
    #    既撑爆磁盘，又长得像备份 —— 把它误当备份用就是灾难。
    try:
        report = ufs.rewrite_container(
            reader,
            [ufs.Replacement(abs_start, abs_start + len(old_blob), new_blob)],
            tmp,
            progress=progress,
            token=token,
        )

        # 闸 3：写后复核 —— 读回新文件，确认改动确实落进去了
        # ⚠️ 比对的是【改前】的字节，不是 src.meta —— 后者已经被改过，
        #    拿它当基准会让判据恒真（这个坑踩过一次）。
        if verify_after:
            _verify_rewritten(tmp, src, old_blob)

        # ⚠️ 替换前必须**关掉我们自己的文件句柄**。
        #    Windows 不允许替换一个正被打开的文件 —— 实测即使是我们自己开的，
        #    也会以 WinError 5（拒绝访问）失败。这就是"检测游戏进程"那道闸的同一个原理。
        src.close()
        try:
            os.replace(tmp, reader.path)
        except OSError as exc:
            # 到这里说明新文件是好的（闸 3 已过），只是放不上去。翻成人话，
            # 并提醒「句柄已关，要重新打开一次」—— 直接再点保存是用不了的。
            raise UserFacingError(
                f"没法替换 {reader.path.name}：{exc.strerror or exc}",
                hint="游戏或别的程序可能正占着这个文件。请关掉占用者，重新打开一次数据源后重试；"
                "本次生成的中间文件已清理，不会留在游戏目录里。",
            ) from None
    except BaseException:
        # 清理失败不能掩盖真正的错误，所以这里再吞一层。
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    return (
        f"已写回 {reader.path.name}（{report.old_size:,} → {report.new_size:,} 字节，"
        f"{report.seconds:.0f} 秒）"
    )


def _verify_rewritten(tmp: Path, src: OpenedSource, old_blob: bytes) -> None:
    """闸 3：写回**新文件**，核对两件事。不通过就**不要替换**，原文件未动。

    判据（两条都要过）：

    1. 期望改动的那几张表，新文件里**确实变了**；
    2. 没打算动的表，新文件里**逐字节没变**。

    ⚠️ 基准必须是**改前的字节** `old_blob`，不能是 `src.meta` ——
    后者已经被改过，拿它当基准会让判据恒真（等于没校验）。这个坑踩过一次，
    当时表现为"写后复核：新文件里没有任何改动"，而实际上新文件是对的。
    """
    before = container.ObjectMeta.parse(old_blob)
    _label_slots(before, _NAMES)
    with ufs.UfsReader(tmp) as r:
        _pid, blob = ufs.find_database_objects(r)[-1]
        after = container.ObjectMeta.parse(blob)
        _label_slots(after, _NAMES)

        want = {s.table: s.data for s in src.meta.tables}
        was = {s.table: s.data for s in before.tables}
        now = {s.table: s.data for s in after.tables}

        missing = [t for t in want if want[t] != was.get(t) and now.get(t) != want[t]]
        if missing:
            raise UserFacingError(
                f"写后复核：这 {len(missing)} 张表的改动没进新文件：{'、'.join(missing[:5])}",
                hint="重写链路有问题。已放弃替换，原文件未动。",
                tables=missing,
            )
        touched = [t for t in want if was.get(t) != now.get(t)]
        unexpected = [t for t in touched if was.get(t) == want.get(t)]
        if unexpected:
            raise UserFacingError(
                f"写后复核：这 {len(unexpected)} 张表被意外改动了：{'、'.join(unexpected[:5])}",
                hint="改动范围超出预期。已放弃替换，原文件未动。",
                tables=unexpected,
            )
        if not touched:
            raise UserFacingError(
                "写后复核：新文件与改动前逐字节相同",
                hint="改动没有落到文件里。已放弃替换，原文件未动。",
            )


def extract_db_zip(src: OpenedSource, target: str | Path) -> Path:
    """从容器或 DataBaseCompiled.json 导出 BA_Mod.zip（就是界面上的「提取」）。"""
    return dbzip.DbZip.from_object(src.meta).write(Path(target))


def load_incoming(path: str | Path, template: container.ObjectMeta | None = None):
    """读一份"要导入的东西"，返回 `(ObjectMeta, 种类)`。

    参数
    ----
    template
        目标容器的形状。**`BA_Mod.zip` 必须给**：它只有明文，没有对象头/尾、pathID、IV，
        没有模板就构不出能写回容器的对象（见 :func:`merge_into_template`）。
        `DataBaseCompiled.json` 自带形状，给了模板则以模板为准（更安全）。
    """
    path = Path(path)
    kind = detect_kind(path)
    if kind is SourceKind.DBC_JSON:
        incoming = container.ObjectMeta.from_json_text(path.read_text(encoding="utf-8"))
        _label_slots(incoming, _NAMES)
        return incoming, kind
    if kind is SourceKind.DB_ZIP:
        if template is None:
            raise UserFacingError(
                "把外部库写进容器需要一个形状模板",
                hint=f"{PACKAGE_NAME} 只有明文，缺对象头/尾与 IV；请先打开目标容器。",
            )
        incoming = dbzip.DbZip.read(path).to_object(template, strict=False)
        return incoming, kind
    raise UserFacingError(
        "不能把容器导入容器",
        hint=f"导入的来源只能是 DataBaseCompiled.json 或外部库 zip（{PACKAGE_NAME}）",
    )


def preview_import(container_path: str | Path, incoming_path: str | Path) -> dict[str, object]:
    """**不写盘**地算一遍"导入会改动哪几张表"。

    ⚠️ 必须与 :func:`import_into_container` 走**同一套合并逻辑**
    （`load_incoming` + `merge_into_template`），否则预演会说假话 ——
    这类"预演与执行各写一套"的分叉是最难查的 bug 之一。
    """
    incoming_path = Path(incoming_path)
    with open_source(container_path) as target:
        if target.reader is None:
            raise AssetError("目标不是容器")
        incoming, kind = load_incoming(incoming_path, template=target.meta)
        merged = merge_into_template(target.meta, incoming)
        by_before = {s.table: s.data for s in target.meta.tables}
        changed = [s.table for s in merged.tables if by_before.get(s.table) != s.data]
        return {
            "container": str(target.path),
            "incoming": str(incoming_path),
            "incoming_kind": kind,
            "path_id": target.meta.path_id,
            "changed": changed,
            "tables": len(merged.tables),
        }


def import_into_container(
    container_path: str | Path,
    incoming: str | Path,
    *,
    progress: Callable | None = None,
    token: object | None = None,
    check_game_running: bool = True,
    check_sha256: bool = True,
    verify_after: bool = True,
) -> str:
    """把 DataBaseCompiled.json 或 BA_Mod.zip **写进** data.unity3d。

    容器在这里扮演两个角色：
    ① 形状模板（对象头/尾、pathID、未改动表要复用的 IV）；
    ② 写入目标。
    """
    incoming = Path(incoming)
    with open_source(container_path) as target_src:
        if target_src.reader is None:
            raise AssetError("目标不是容器")
        # 与 preview_import 走**同一套**读取与合并，免得预演和执行分叉。
        replacement, _kind = load_incoming(incoming, template=target_src.meta)
        target_src.meta = merge_into_template(target_src.meta, replacement)
        return save(
            target_src,
            progress=progress,
            token=token,
            check_sha256=check_sha256,
            check_game_running=check_game_running,
            verify_after=verify_after,
        )


def merge_into_template(
    template: container.ObjectMeta, incoming: container.ObjectMeta
) -> container.ObjectMeta:
    """把导入的数据并到模板上：**只取数据，形状用模板的**。

    外部库 zip 没有对象头/尾/IV，所以必须这样合。DataBaseCompiled.json 自带形状，
    但它可能与目标容器的形状不同（比如来自别的游戏版本），以模板为准更安全。
    """
    by_table = {s.table: s for s in incoming.tables}
    slots = []
    for tpl in template.tables:
        inc = by_table.get(tpl.table)
        if inc is None:
            slots.append(tpl)
            continue
        # 保留 IV 的规则只有一份：见 container.slot_with_data。
        slots.append(container.slot_with_data(tpl, inc.data))
    return container.ObjectMeta(
        object_head=template.object_head,
        object_tail=template.object_tail,
        tables=slots,
        path_id=template.path_id,
        source_sha256=template.source_sha256,
    )


def describe(src: OpenedSource) -> dict[str, object]:
    """给界面用的摘要。"""
    return {
        "kind": src.kind.value,
        "label": src.kind.label,
        "path": str(src.path),
        "path_id": src.meta.path_id,
        "tables": len(src.meta.tables),
        "rows": sum(len(s.json_payload()) for s in src.meta.tables),
        "dirty": [s.table for s in src.meta.tables if s.dirty],
    }
