"""保存：把一个实例写成游戏认的 BA_Mod.zip（零 Qt）。

## 格式（本轮**定死**的一条，交接 §5-5 要求先定）

```
BA_Mod.zip                       ← 平铺，条目名 = <表名>.json
├── Units.json
├── …                            （见 core/asset/tables.py 的 24 个 zip_entry）
├── PlaneFlyPresets.json         ← FlyPresets 的主条目名
└── PlaneFlyPreset.json          ← ❗ 同一份字节再来一份（**无 s**）
```

**为什么飞行预设要写两份**（2026-10-02 实机翻案）：游戏取行时按**模型类型名**
`typeof(T).Name` 查字典（`GetTableRows<T>`，ISIL 5939-5962），那张表的类型叫
`Table<PlaneFlyPreset>`（单数）；只写复数那一份，`ReadTablesFromZip` 查不到就
`continue` 掉 ⇒ 飞行预设表空 ⇒ `LoadUnits.LoadMobility` NRE ⇒ **军械库一张卡都没有**。
工坊里那份已知可用的 db.zip 就是两个名字都写、内容相同。多写的条目游戏静默跳过，无副作用。

**格式没变**，因为它就是游戏读的那个格式（kb/04 §5.2 实测）：
`ZipFile.OpenRead` 按条目名分派，不认的条目**静默跳过**，缺哪张表就不加载哪张表。

## 为什么「一个 zip 装两份实例」做不到（这是本轮的新结论）

补丁把 `DataBaseCompiled.LoadSource()` 的**两处调用点**（`Load()` 与
`LoadCompiled()`）都换成了同一句：`DataBaseSourceData("<游戏根目录>/BA_Mod.zip", true)`
（kb/11 §3 的 cave 反汇编，两个插桩点逐条核对过）。
也就是说：**两个容器对象拿到的是同一个包**，包里没有"这份数据属于哪个 pathID"的落点。

于是备选格式都不成立：

| 备选 | 结果 |
|---|---|
| 子目录 `52085/Units.json` | 游戏按 `Units.json` 查，查不到 -> **两张表都不加载** |
| 两份 zip（BA_Mod.zip + BA_Mod_52082.zip） | 游戏只读固定名 BA_Mod.zip，第二份永远不生效 |
| 平铺 + 第二份塞进未知条目 | 游戏静默跳过；我们的工具能读回来，但**游戏侧没有落点**，等于假装写了 |

所以本轮的定案是：**一份 zip 装一份实例，平铺，格式不动**；
界面顶栏只写「实例 52085」，不写「52085 + 52082」。
战役库（52082）要生效，得先扩展 **DLL 补丁**（让 `LoadCompiled()` 那个插桩点
认第二个文件名）—— 那是补丁那一轮的活，不是保存路径能解决的。
这条限制同时写进了 docs/设计-单位编辑器-双击进入.md §9。

## 「未动的表逐字节没变」

未改动的表**不做反序列化再序列化**，直接把源文件字节搬进 zip ——
实测踩过：`Mobility` 的明文里有个 `1.5E+08`，Python 的 json 只会输出
`150000000.0`，一读一写字节就变了（这条实测事实现在记在
app/shell/db_channel.py 的模块 docstring 里 —— 那里原来的 `_BASELINES` 快照已随
「保存通道只留一份」一并删除）。只有**内容真的变了**的表才重新序列化。

## 「未改动的表」从哪拿字节：一个实例只有一份来源

既然未改动的表要逐字节搬，那**这些字节从哪来**就只有一个答案，否则"包里没动过的表"
会随打开方式而不同：

| 打开方式 | 原始字节在哪 | 谁提供 |
|---|---|---|
| `DataBaseCompiled/<pid>/` 目录 | `directory/<条目名>.json` | 磁盘散文件 |
| `BA_Mod.zip` / 容器 / 单个 json | 打开时读到的那份明文 | `Instance.original_bytes` |

那一行的那三种来源由 `app/shell/db_channel.py::dataset_from_source()` 装进 GUI，它顺手把
`OpenedSource.meta.tables[*].data` 存进 `Instance.original_bytes` ——
**那就是 `core/session.py._save_dbzip()` 写回时用的同一份字节**。
于是 CLI 的 `session.save()` 与 GUI 的自动保存收敛到同一份来源，不是各读各的。

为什么是"把字节存在实例上"，而不是"记下 zip 路径、保存时再读一遍"：

1. **同一份 > 同一处文件。** 再读一遍就多出一个可能与"打开时"不一致的来源
   （这中间文件被别的程序改过、或用户换了包）；存下来的那份就是打开时的那份。
2. **不猜。** 不能靠"这个目录里有没有 zip"定来源：一个目录里可能有好几个包，
   猜错等于**把另一个包的字节静默写进新包** —— 错数据比报错糟得多。
3. **来源跟着实例走。** `plan_package()` 是零 Qt 的 core 函数，手上只有一个 `Instance`；
   把来源挂到别处（比如界面层按 `id()` 索引的缓存）会让保存依赖界面层的生命周期，
   也没法单独测。
4. **不塞进 `Table.source_path`。** 那个字段的语义是"这张表从哪个文件来"，
   `app/widgets/table_model.py` 会 `read_text()` 它；zip 里的条目/容器的明文字段
   都不是可读路径，塞进去会把"重读整表"这条路变成假路径。

取字节的顺序：**散文件 → `Instance.original_bytes` → 报错**。
实现只有一份 —— `core/data/loader.py::locate_table_bytes`：保存与"重读整表"
（`Instance.reload_table`）都调它，否则迟早出现"存进去的字节和重读回来的不是一个来源"。
散文件优先是为了让 `DataBaseCompiled` 那条路一个字节都不变；
两个都没有时**报错，绝不静默重新序列化**（那正是"没动过的表字节也变了"那个坑）。

## 「包里多出来的未知条目」必须跟回去

打开一个**别人的包**再保存时，包里可能有我们那 24 张表之外的条目
（`dbzip.DbZip.read()` 把它们收进 `extra`：工坊里就有人多塞一个单数的
`PlaneFlyPreset.json`）。游戏会静默跳过它们，所以：

- **写回原包时必须原样带上** —— 删掉就是**静默改别人的东西**，而且没有任何症状；
- 计划是"这次要写什么"的唯一描述，所以它们进 `PackagePlan.extra`
  （**默认**取 `Instance.opened_file.extra`，见 :func:`plan_package`），
  `write_package` / `replace_package` 一起写、`verify_package` 一起验。

**为什么是"让 plan 带上 extra"而不是"写回时走 dbzip 那条路"**：`DbZip.write()`
是**原地写**（没有临时文件、没有原子替换、没有写后复核），而写回用户自己那个包
恰恰是最不能写坏的一步。让 `DbZip.write` 也长出这三样，等于把同一个写包实现做两遍
（本项目在别处已经吃过"两套实现分叉"的亏，见 `core/asset/container.py::slot_with_data`
的说明）。所以写包只有一条实现：本模块的 plan -> write -> verify。

## 落点：写回"你打开的那个文件"

口径与唯一实现见 :func:`default_save_target`。要点：

- **打开的是文件**（`BA_Mod.zip` / `DataBaseCompiled.json`）→ 写回那个文件本身；
- **打开的是目录** → 仍写 `<该目录的上一级>/BA_Mod.zip`（行为不变）；
- **`data.unity3d`** → 也写外部包（重写 7.1 GB 不能挂在"改一格就自动保存"上）。

写回原文件用 :func:`replace_package`（**先复核、后替换**）；写到别处用
:func:`write_package`。两条路都**不生成任何 `.bak` / 副本**（用户口径：
"别学 .bak，相当垃圾，我自己会备份"）。
"""

from __future__ import annotations

import json
import os
import zipfile
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from ba_mod_maker.core.asset import tables as table_names
from ba_mod_maker.core.asset.tables import resolve_table_name
from ba_mod_maker.core.data.dataset import FORMAT_ZIP, Instance, Table
from ba_mod_maker.core.data.loader import locate_table_bytes
from ba_mod_maker.core.errors import DataSourceError, UserFacingError

#: 包的格式标识。**写进包里的一行小字**？不 —— 游戏会跳过未知条目，
#: 但也可能有人拿它当数据；所以格式信息只留在这个常量与文档里，不进包。
PACKAGE_FORMAT = "flat-v1"

#: 我们产出的包名（与 core/session.py 的 PACKAGE_NAME 同源，这里不 import 它：
#: session 会拉起 asset/container 那一条重链，而保存路径只需要一个字符串）。
PACKAGE_NAME = "BA_Mod.zip"


@dataclass(frozen=True, slots=True)
class PackagePlan:
    """一次保存的计划：每个条目写什么字节（内容已定，**还没落盘**）。"""

    instance_id: str
    #: (条目名, 字节)，按 24 张表的声明顺序。
    entries: tuple[tuple[str, bytes], ...]
    #: 重新序列化的表（内容变了）。
    changed: tuple[str, ...]
    #: 逐字节搬过来的表（内容没变）。
    untouched: tuple[str, ...]
    #: 成员名 -> 写进 zip 头里的 DOS 时间 `(年, 月, 日, 时, 分, 秒)`。空 = 用「现在」。
    #:
    #: 为什么要有它：`zipfile.writestr(name, blob)` 的时间戳取**当前时刻**，
    #: 于是「同一份内容写两次」的产物在**偏移 0xA**（DOS 时间字段）开始就不同
    #: （实测：324,685 B 的两份，成员内容/CRC/压缩字节全同，首差 0xA）。
    #: 后果是「删 3 行再插回 3 行 ⇒ 输出与输入逐字节相同」**永远不成立** ——
    #: 逐字节可逆是本产品的口径（`row-delete` / `row-restore` 往返靠它），
    #: 而写包的**唯一**实现就是本模块，所以修在这里，不在调用方各补一份。
    #:
    #: 来源只有一处：:func:`member_stamps`。缺谁就退回「现在」（不报错：
    #: 这只是一行元数据，为它挡住一次保存不值得）。
    stamps: Mapping[str, tuple[int, int, int, int, int, int]] | None = None

    #: 跟着写回去的**未知条目**（条目名, 字节），按打开时的顺序。
    #:
    #: 为什么它在计划里、而不是"落盘时再补"：写回**用户打开的那个包**时，包里那些
    #: 我们不认识的条目（`dbzip.DbZip.extra`）必须原样跟回去 —— 游戏会静默跳过它们，
    #: 删掉却可能弄坏别人的东西，而且**没有任何症状**。计划是"这次要写什么"的唯一描述，
    #: 少一样就会在某个分支里静默丢。
    extra: tuple[tuple[str, bytes], ...] = ()

    @property
    def entry_names(self) -> tuple[str, ...]:
        """24 张表的条目名（**不含**未知条目）。"""
        return tuple(name for name, _ in self.entries)

    @property
    def extra_names(self) -> tuple[str, ...]:
        """跟回去的未知条目名。"""
        return tuple(name for name, _ in self.extra)

    @property
    def all_entries(self) -> tuple[tuple[str, bytes], ...]:
        """**真正会写进 zip** 的全部条目：24 张表，然后是跟回去的未知条目。

        顺序与 `dbzip.DbZip.write()` 一致 —— 游戏按条目名查表，顺序不影响加载，
        固定顺序只是为了产物可比对。
        """
        return self.entries + self.extra

    @property
    def total_bytes(self) -> int:
        return sum(len(blob) for _, blob in self.all_entries)


@dataclass(frozen=True, slots=True)
class PackagePolicy:
    """保存口径（界面拿它渲染顶栏那句话与悬浮说明）。"""

    format: str
    #: 这一次要写的实例 id。
    instance_id: str
    #: **写到哪个文件**（绝对路径）。用户口径要求这句话里能看出落点，
    #: 不能退化成只有文件名 —— 打开的是哪个包、写回的就得是哪个包。
    target: str
    #: 界面上顶栏那行字。
    headline: str
    #: 悬浮说明：为什么只有一份实例。
    reason: str


def plan_package(
    instance: Instance,
    *,
    changed_tables: tuple[str, ...] | list[str] | None = None,
    extra: Mapping[str, bytes] | None = None,
    stamps: Mapping[str, tuple[int, int, int, int, int, int]] | None = None,
) -> PackagePlan:
    """算出这次保存要写的条目（24 张主条目 + 别名条目 + 跟回去的未知条目）。

    `changed_tables` 留空 = 全部按"没变"处理（逐字节搬源文件）——
    那正是"另存为一份原样包"的语义。

    `extra` 留空（`None`）= 取 `instance.opened_file.extra`：**默认就把原包里那些
    未知条目带上**。写回用户打开的那个包时，"带上"必须是默认行为 —— 漏一次就是
    静默删掉别人包里的东西，而且删掉之后没有任何症状（游戏本来就跳过它们）。
    显式传 `{}` 才是"这次不要它们"（导出 / 新建包）。

    `stamps` 留空（`None`）= **行为与从前逐字节相同**（每个成员的时间戳取"现在"）；
    给了（见 :func:`member_stamps`）就按成员名沿用那份时间戳 ——
    这是「写回一个包之后仍与源包逐字节相同」的**唯一**前提（缺了它，同一份内容
    写两次的头字段就不同）。⛔ 别在调用方各写一份读时间戳的代码。

    **不给（`None`）且打开的是 zip** ⇒ 自动沿用**那个 zip** 的时间戳
    （见 :func:`_path_stamps`）。这是默认，不是可选项 —— 理由见下。
    """
    # 没显式给时间戳、且打开的是 zip ⇒ 沿用**那个 zip** 的。
    #
    # 为什么默认就该这样（2026-10-12 定，Lead 的方案乙）：
    # 从前每个成员的时间戳都取"现在"，于是「同一份内容写两次」的产物在**偏移 0xA**
    # 起就不同（实测：成员内容 / CRC / 压缩字节全同，首差 0xA）。后果是
    # 「删 3 行再插回 3 行 ⇒ 输出与输入逐字节相同」**永远不成立** ——
    # 而这正是产品对用户承诺的可逆性（反向 Δ 还原）。
    # 「改写用户打开的那个包」本来就不该顺手把它每个成员的时间戳改成现在。
    #
    # ⚠️ 这里只读**中央目录的时间戳**这一样东西，不读表内容 ——
    # 模块开头那条「同一份字节 > 同一处文件」的口径没有被破坏：表内容仍然只来自
    # `Instance.original_bytes`，本条不构成"第二个内容来源"。
    # 文件没了 / 不是 zip ⇒ `_path_stamps` 返回 `{}`，逐字节退回"用现在"（不报错：
    # 为一行元数据挡住一次保存不值得，而且这里本来就没有"复核不过"可言）。
    # 目录来源 / json 来源 / data.unity3d 的 `opened_file` 是 None 或 format != zip
    # ⇒ 这一整条一个字都不变。
    if stamps is None:
        stamps = _path_stamps(instance)

    carried = _resolve_extra(instance, extra)
    # ⚠️ 这个集合装的是 **_resolve() 之后的「真名」** —— 也就是**这份数据源里**
    #    这张表叫什么（目录来源按文件名建表：FlyPresets 那张在这里叫
    #    PlaneFlyPresets）。所以下面比较时**必须拿同一个真名**去比，
    #    不能拿 naming.table（权威名）比：两边在目录来源上永不相等。
    changed = {_resolve(instance, name) for name in (changed_tables or ())}
    entries: list[tuple[str, bytes]] = []
    did_change: list[str] = []
    untouched: list[str] = []

    for naming in table_names.TABLES:
        name = _resolve(instance, naming.table)
        if name is None:
            raise DataSourceError(
                f"数据源里没有 {naming.table} 这张表",
                hint=f"{PACKAGE_NAME} 必须是完整的 24 张表：缺哪张游戏就不加载哪张",
                table=naming.table,
            )
        # 比的是 name（上面 _resolve() 出来的真名），不是 naming.table（权威名）——
        # 曾经这里写的是后者，于是「目录来源 + 改 FlyPresets 那一张」恒为假：
        # 改动被当成「没变」，走下面那支把**源字节照搬**进包，**改动静默丢失**。
        if name in changed:
            blob = serialize_table(instance.table(name))
            did_change.append(naming.table)
        else:
            blob = _original_bytes(instance, name, naming.zip_entry)
            untouched.append(naming.table)
        entries.append((naming.zip_entry, blob))
        # ❗ 别名条目（同一份字节，紧跟主条目）：游戏取行时按**模型类型名**查表，
        #    飞行预设那张的类型是单数 `PlaneFlyPreset` —— 少写这一份，游戏就
        #    静默跳过那张表 ⇒ LoadMobility NRE ⇒ 军械库空白。见 tables.ZIP_ENTRY_ALIASES。
        entries.extend((alias, blob) for alias in naming.alias_entries)

    return PackagePlan(
        instance_id=instance.path_id,
        entries=tuple(entries),
        changed=tuple(did_change),
        untouched=tuple(untouched),
        stamps=None if not stamps else {name: tuple(when) for name, when in stamps.items()},
        extra=carried,
    )


def write_package(plan: PackagePlan, path: str | Path) -> Path:
    """落盘：临时文件 + 原子替换（写一半就断电不会留下半个包）。

    ⚠️ 替换发生在**复核之前** —— 落点是"另存"的新文件时没问题（复核不过也没有谁被
    弄坏，报错即可）。要写回**用户打开的那个包**时请用 :func:`replace_package`。
    """
    target = Path(path)
    tmp = _tmp_path(target)
    try:
        _dump(plan, tmp)
        os.replace(tmp, target)
    except BaseException:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    return target


def replace_package(plan: PackagePlan, path: str | Path) -> Path:
    """**先复核、后替换**：把计划写进同目录的临时文件，读回来逐条比对，过了才动原文件。

    与 :func:`write_package` 的差别只有顺序，但它决定了"复核不通过时谁被弄坏"：

    - 写回**用户打开的那个包**（他自己的东西，我们没有第二份）必须用这一条 ——
      复核不过时**原文件一个字节都没动**；
    - 复核本身是"读回来逐条比"（AGENTS §5：写完真实文件一定复核）。

    临时文件与目标同目录（同一个卷，`os.replace` 才是原子的），失败时一定清掉 ——
    它长得像备份，留在用户目录里既占地方又会被误当备份用。
    """
    target = Path(path)
    tmp = _tmp_path(target)
    try:
        _dump(plan, tmp)
        problems = verify_package(plan, tmp)
        if problems:
            raise UserFacingError(
                "写后复核不通过：" + "；".join(problems),
                hint="原文件未动（还是你原来那一份）。请重试；反复出现就把日志一起报上来。",
            )
        os.replace(tmp, target)
    except BaseException:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    return target


def _tmp_path(target: Path) -> Path:
    """落盘用的临时文件名。**与目标同目录**（同卷才能原子替换）。

    它是"写一半"的中间件，不是备份：成功路径上 `os.replace` 会把它用掉，
    失败路径上调用方会删掉它（用户口径：不要任何 `.bak` / 副本）。
    """
    return target.with_suffix(target.suffix + ".tmp")


def member_stamps(path: str | Path) -> dict[str, tuple[int, int, int, int, int, int]]:
    """读一个 zip 里每个成员的 **DOS 时间戳**（条目名 -> `(年, 月, 日, 时, 分, 秒)`）。

    **唯一入口**：要保住"写回之后与源包逐字节相同"的调用方都从这里拿，
    ⛔ 别在 CLI 里再写一份读时间戳的代码（两份迟早分叉，而分叉的症状是
    "产物与源包差几个字节"，最难查的那一类）。

    直接喂给 :func:`plan_package` 的 `stamps`（两个函数取的可迭代对象本就相同）：

        plan_package(instance, changed_tables=…, stamps=member_stamps(source))

    目录条目（`is_dir()`）也有时间戳，一并收 —— 复用者按条目名取即可。
    """
    with zipfile.ZipFile(Path(path)) as archive:
        return {info.filename: info.date_time for info in archive.infolist()}


def _zip_info(name: str, when: tuple[int, int, int, int, int, int] | None) -> zipfile.ZipInfo:
    """一个成员的头：**时间戳要么沿用给定的、要么退回「现在」**（增量改在 `_dump` 一处）。

    `date_time` 必须在构造时就给 —— `writestr` 里再设已经晚了（实测：设了照样是"现在"）。
    那 6 个数必须是 0..59 的秒级值；`tuple(...)` 收下 `zipfile` 放出来的那个 6 元组，
    这样 `member_stamps()` 的返回值可以原样喂回来。
    """
    # ⚠ 没给时间戳时**不能传 `date_time=None`** —— `ZipInfo.__init__` 会当场
    #   `TypeError: 'NoneType' object is not subscriptable`
    #   （zipfile 里那句 `date_time[0] < 1980`）。
    #   ZipInfo 自己的默认就是 (1980,1,1,0,0,0)，"退回默认"的唯一做法是**不传这个 kwarg**。
    #   实测：这一行写错过一次，症状是"源包里缺某个成员的时间戳时，整条保存炸掉"。
    info = zipfile.ZipInfo(name) if not when else zipfile.ZipInfo(name, date_time=tuple(when))
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _dump(plan: PackagePlan, path: Path) -> None:
    """把计划的全部条目写成一个 zip（24 张表 + 未知条目）。

    时间戳取自 `plan.stamps`（见 :func:`member_stamps`）；没给就与从前一样取"现在"。
    """
    stamps = plan.stamps or {}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, blob in plan.all_entries:
            archive.writestr(_zip_info(name, stamps.get(name)), blob)


def verify_package(plan: PackagePlan, path: str | Path) -> tuple[str, ...]:
    """**写完读回来逐条比**（AGENTS §5：写完真实文件一定复核）。

    返回不一致的描述；空元组 = 通过。
    """
    problems: list[str] = []
    target = Path(path)
    try:
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            # 24 张表**和**跟回去的未知条目都逐条比：未知条目也是"这次要写的东西"，
            # 复核漏了它就等于"保留未知条目"这件事没人验（用户口径里它在验收判据里）。
            for name, blob in plan.all_entries:
                if name not in names:
                    problems.append(f"少了条目 {name}")
                    continue
                if archive.read(name) != blob:
                    problems.append(f"条目 {name} 的内容与计划不一致")
            expected = set(plan.entry_names) | set(plan.extra_names)
            extra = [n for n in names if n not in expected]
            if extra:
                # 多出来的条目不算错（我们本来就会保留别人的额外条目），但要说出来。
                problems.append("多出条目：" + "、".join(extra))
    except (OSError, zipfile.BadZipFile) as exc:
        problems.append(f"读不回来：{exc}")
    return tuple(problems)


def read_package_rows(path: str | Path) -> dict[str, list]:
    """读一个包里的表（给"保存后核对改动落进去了"用）。键是**表名**。"""
    by_entry = {naming.zip_entry: naming.table for naming in table_names.TABLES}
    out: dict[str, list] = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            table = by_entry.get(info.filename.rsplit("/", 1)[-1])
            if table is None:
                continue
            out[table] = json.loads(archive.read(info).decode("utf-8"))
    return out


def default_save_target(instance: Instance) -> Path:
    """自动保存的落点：**打开的是文件就写回它本身**，否则写在数据源同级目录。

    用户口径（2026-09-30）：

        「保存写回**你打开的那个文件**」；
        「别学 .bak，相当垃圾，我自己会备份，它凭空占我的空间」。

    | 打开的是 | 落点 |
    |---|---|
    | `BA_Mod.zip` / `DataBaseCompiled.json` | **那个文件本身**（同一个路径，原子替换） |
    | `DataBaseCompiled` 目录 | `<该目录的上一级>/` + `BA_Mod.zip`（行为一个字节不变） |
    | `data.unity3d` | 同上（那条路的"写回"是 7.1 GB / 约 4 分钟，不能挂在自动保存上） |

    ⚠️ **落点只有这一份实现**：`app/shell/edit_session.py::default_save_path()` 与
    :func:`policy_for` 都调它。两处各写一遍迟早会分叉，症状是"胶囊说写到 A、
    实际写到 B"—— 用户刚刚就是被这个坑到的（打开 `测试文件\\BA_Mod.zip`，
    自动保存却落到工作区根）。
    """
    opened = instance.opened_file
    if opened is not None:
        return Path(opened.path)
    return Path(instance.directory).parent / PACKAGE_NAME


def policy_for(instance: Instance) -> PackagePolicy:
    """顶栏那句话 + 悬浮说明。**界面不要自己拼这句话**（口径只有一份）。

    "写到哪个文件"也在这里：打开的是文件时写回**那个文件**，打开的是目录时写
    同级目录的 `BA_Mod.zip`。那句话里带的是**完整路径**，不是只有文件名 ——
    用户要能一眼看出这一下存到哪儿去了。
    """
    target = default_save_target(instance)
    opened = instance.opened_file
    base = (
        "游戏的两个加载入口（Load / LoadCompiled）读的是同一个 "
        f"{PACKAGE_NAME}，包里没有「这份数据属于哪个 pathID」的落点，"
        "所以一个包只能装一份实例。战役库要生效得先扩展 DLL 补丁。"
    )
    if opened is None:
        return PackagePolicy(
            format=PACKAGE_FORMAT,
            instance_id=instance.path_id,
            target=str(target),
            headline=f"实例 {instance.path_id} · 保存写 {PACKAGE_NAME}",
            reason=f"自动保存落点：\n{target}\n\n{base}",
        )
    kept = f"原包里 {len(opened.extra)} 个我们不认识的条目会原样保留。" if opened.extra else ""
    return PackagePolicy(
        format=PACKAGE_FORMAT,
        instance_id=instance.path_id,
        target=str(target),
        headline=f"实例 {instance.path_id} · 保存写回 {opened.path.name}",
        reason=f"保存写回**你打开的那个文件**：\n{target}\n{kept}\n\n{base}",
    )


def _path_stamps(instance: Instance) -> dict[str, tuple[int, int, int, int, int, int]]:
    """默认时间戳：**你打开的那个 zip** 的成员时间戳（拿不到就给空 = 退回"现在"）。

    调用方不用记着传 —— `row-delete` / `row-restore` / GUI 保存走的都是
    :func:`plan_package`，一条实现，没有"漏了一个入口"这种漏法。

    ⛔ 三种情况**必须**一个字都不变（它们本来就没有 zip 成员时间戳这回事）：
    目录数据源（`opened_file is None`）· `DataBaseCompiled.json`（format 不是 zip）·
    `data.unity3d`（`opened_file is None`）。
    """
    opened = instance.opened_file
    if opened is None or opened.format != FORMAT_ZIP:
        return {}
    try:
        return member_stamps(opened.path)
    except (OSError, zipfile.BadZipFile):
        # 文件被删 / 被换成不是 zip 的东西：这不是"复核不过"，只影响一行元数据。
        return {}


def _resolve_extra(
    instance: Instance, extra: Mapping[str, bytes] | None
) -> tuple[tuple[str, bytes], ...]:
    """这次要跟回去的未知条目。`None` = 取打开时那个包里带的（默认；见 plan_package）。"""
    if extra is not None:
        return tuple(extra.items())
    opened = instance.opened_file
    if opened is None:
        return ()
    return tuple(opened.extra.items())


def _resolve(instance: Instance, table: str) -> str | None:
    """表名归一（数据源可能按文件名建表：FlyPresets <-> PlaneFlyPresets）。"""
    return resolve_table_name(table, instance.table_names)


def _original_bytes(instance: Instance, table: str, entry: str) -> bytes:
    """未改动表的**原始字节**。顺序：散文件 -> `Instance.original_bytes` -> 报错。

    **顺序本身不在这里** —— 在 `core/data/loader.py::locate_table_bytes`：
    保存与"重读整表"（`Instance.reload_table`）必须是同一份规则。

    `entry` 是权威条目名（`naming.zip_entry`，`FlyPresets` 那张是
    `PlaneFlyPresets.json`），也是 `original_bytes` 的键 ——
    用条目名而不是表名做键，是因为同一张表在两种数据源里有两个表名
    （见 `core/asset/tables.py` 的表名别名），条目名是唯一没有歧义的那个。

    找不到时**报错而不是重新序列化** —— 静默重新序列化正是"未动的表字节变了"那个坑。
    报错分两种，因为用户要能看懂"这个来源能不能存"：
    来源压根没有可复用的字节 vs 来源有字节但缺这一条。
    """
    found = locate_table_bytes(instance.directory, table, entry, instance.original_bytes)
    if found is not None:
        return found.blob
    if instance.original_bytes is not None:
        raise DataSourceError(
            f"打开时那份来源里没有 {entry}",
            hint="包 / 容器里应当有全部 24 张表；缺条目说明来源不完整，"
            "或表名与 core/asset/tables.py 的权威清单对不上",
            table=table,
        )
    raise DataSourceError(
        f"找不到 {table} 的源文件（{instance.directory}）",
        hint="保存要把未改动的表**逐字节**搬进新包，字节只能来自打开时那份来源；"
        "这个来源既没有磁盘上的 JSON，也没有可复用的原始字节，所以**存不了**。"
        "请用「打开…」重新选 data.unity3d / DataBaseCompiled.json / BA_Mod.zip 之一。",
        table=table,
    )


def serialize_table(table: Table) -> bytes:
    """把改过的表序列化成条目内容（也用于写回 `DataBaseCompiled.json`）。

    键顺序 = 列序（游戏声明序）。**不加空格**（紧凑）并与源文件一样用 UTF-8。
    """
    text = json.dumps(rows_as_dicts(table), ensure_ascii=False, separators=(",", ":"))
    return text.encode("utf-8")


def rows_as_dicts(table: Table) -> list[dict]:
    """`Table` 的行（按列序的 list）还原成 `[{列: 值}]`。

    注意：**键顺序必须与列序一致**，否则写回去之后列序会变 ——
    实测容器的明文是按游戏声明序排的，列序一变整张表的字节就全变了。
    `json.dumps` 会保留 dict 的插入顺序，`zip` 出来正好是列序。

    这一份实现同时给三条路用：写包（`serialize_table`）、写回 json
    （`core/session.py`）、把 GUI 的表同步回容器对象
    （`app/shell/db_channel.py::apply_dataset_to_source`）—— 从前那两处各写了一遍。
    """
    return [dict(zip(table.columns, row, strict=False)) for row in table.rows]


__all__ = [
    "PACKAGE_FORMAT",
    "PACKAGE_NAME",
    "PackagePlan",
    "PackagePolicy",
    "default_save_target",
    "member_stamps",
    "plan_package",
    "policy_for",
    "read_package_rows",
    "replace_package",
    "rows_as_dicts",
    "serialize_table",
    "verify_package",
    "write_package",
]
