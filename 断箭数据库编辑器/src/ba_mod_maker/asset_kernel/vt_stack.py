"""VT 组指向通道 · 产品化入口：**修改材质里 `m_BuildTextureStacks` 的两个既有字段**（等长写回）。

## 这个模块收的是哪一件手写研究件

`_work/paint-design/vt_install.py`（2026-10-10 的研究件）把
「T-72B 默认材质指向**我们自己烘的 VT 组**」做成了一次等长改动。
那一套的**口径**（四条自证 + 反向 Δ）在这里按产品口径重写；
研究件自己的东西（硬编码路径、直写游戏目录、无判据的 `print`）**一概不带进来**
（kb/19 §1：要复用的逻辑搬进产品，`_work` 里只留调用它的那一行）。

## 真机结论（依据，不是我推的）

* `kb/18` §6.2 / §6.3 ＋ `docs/设计-材质完全自定义.md` §2.7：**自建组被游戏接受**。
  编辑器烘出池子（`<group>.gts` ＋ `<group>0_1/2/3/4.gtp`，放进
  `BrokenArrow_Data\\StreamingAssets\\`）＋ 等长改材质的 `m_BuildTextureStacks`
  ⇒ 游戏照我们给的数据渲：两轮实机对照（Mask 的 A=255 ⇒ 用户判「很光滑」；
  A=0 ⇒ 判「哑光了」），两轮都**没动** `_Metal`/`_Smoothness` 标量。
* `kb/18` §6.3：`m_BuildTextureStacks` 的两个字段是 **string**，值由**编辑器在构建期自己算出来**
  （32 位小写 hex）⇒ **名字规则不用我们猜**，我们只负责把值改对。

## 这一层做什么

    unit_key -> catalog 定位 -> 唯一命中包（m_Container 复核）-> 材质 typetree
    -> 改 m_BuildTextureStacks 的 groupName / itemName（**两个既有字段的值**，AGENTS §0.10「修改」）
    -> 等长写回（L1）＋ 同步 catalog（CRC 成对）
    -> 反向 Δ 补丁落工程目录（ADR 0007 第 4 条，⛔ 不拷整包）

## 不做什么

* ⛔ **不搬池子文件**：`.gts/.gtp` 是「**新增文件**」（AGENTS §0.10），走的是另一条通道
  （`pool_status` 只**读**一眼它在不在，⛔ 一个字节都不写）—— 本模块只做材质那一跳；
* 不换像素、不加删对象、不加资产地址（ADR 0007 第 7 条的能力边界）；
* 不自造写回器 —— 写回那一趟全在 `writer` 里（`patch_store` 给了就走反向 Δ）。

## 规矩

* 纯函数（`stack_entries` / `set_stack`）**零第三方依赖**，可以单独测；
* 干跑（`plan_install` / `install(dry_run=True)`）**一个字节都不写、不建补丁**；
* 栈表 / 材质名**一律不许猜**：读不出来、或不**恰好一项**，抛 `AssetError` 并说清可用值；
* **新旧相同 = 空改动 ⇒ 拒绝**（空改动不该写盘）。

## 四条自证（照抄 `writer.build_material_change` 的风格，都在 `build_stack_change` 里做）

① 不改动的 typetree 往返**逐字节相同**（L1 的前提）· ② 改完**等长** · ③ 从**新字节读回**的
group/item == 新值 · ④ **新旧相同 ⇒ 抛错**。任何一条不成立都抛，⛔ 不许硬写。

⚠️ **② 比看上去弱，所以 ③ 才是主判据**：等长只保证"没给游戏换长度"，它**不保证值对**
（值对不对是 ③ 的事）。两条都要在，缺一条这条链就没有自证。

## 私有件复用（将来并入 `object set`）

`skin_install._open_unit` / `_materials_of` / `_find_material` 是**私有函数**，本模块直接调它们
—— 「unit_key -> 唯一命中包 -> prefab 的材质表」这把尺子**只有一份**，不在这里复制第二份。
等 `docs/adr/0008-资产编辑三操作.md` 的 `object set`（三动词：`set` / `add` / `delete`）落地，
这三条搬进那个通用入口，本模块改成调它；在那之前 ⛔ 不许各写一份 `locate`。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import reverse_delta, skin_install, writer
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "GROUP_FIELD",
    "ITEM_FIELD",
    "STACK_FIELD",
    "STREAMING_SUBDIR",
    "PoolFile",
    "StackEntry",
    "VtStackPlan",
    "build_stack_change",
    "install",
    "plan_install",
    "pool_status",
    "set_stack",
    "stack_entries",
    "streaming_dir_of",
]

#: 材质 typetree 里那张 VT 栈表（`kb/18` §6.3：两个字段类型是 **string**）。
STACK_FIELD = "m_BuildTextureStacks"
#: 栈表里那一项的两个字段名（typetree 里的原样拼写）。
GROUP_FIELD = "groupName"
ITEM_FIELD = "itemName"

#: 池子文件在游戏盘上的落点（`kb/18` §6.2 实测：与构建输出的位置一致）。
STREAMING_SUBDIR = ("BrokenArrow_Data", "StreamingAssets")

#: 「把一串对象字节按同一个 typetree 解回来」的接口：`(对象, 新字节) -> 树`。
TreeReader = Callable[[Any, bytes], Mapping[str, Any]]


# --------------------------------------------------------------------------- #
# 纯函数：树（零第三方依赖，可单独测）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class StackEntry:
    """VT 栈里的一项：`groupName` ＋ `itemName`（编辑器在构建期算出来的两个名字）。"""

    group: str
    item: str

    def to_tree(self) -> dict[str, str]:
        """按 typetree 的字段名拼回一项（写回时用）。"""
        return {GROUP_FIELD: self.group, ITEM_FIELD: self.item}


def stack_entries(tree: Mapping[str, Any]) -> tuple[StackEntry, ...]:
    """**纯函数**：读一个材质 typetree 里的 VT 栈表。

    形状不对（字段不在 / 不是序列 / 项不是 Mapping / 两个字段不是字符串）一律抛
    `AssetError`，⛔ **不静默当空** —— 把"读法坏了"写成"这个材质没有 VT"，
    下一步就是拿一个假的空栈去写盘。
    """
    raw = tree.get(STACK_FIELD)
    if raw is None:
        raise AssetError(
            f"这个材质没有 {STACK_FIELD}",
            hint="没有栈表的不是 VT 材质（或读到的根本不是材质 typetree）—— ⛔ 不许当空栈往下写",
        )
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise AssetError(
            f"{STACK_FIELD} 不是序列（读到 {type(raw).__name__}）",
            hint="typetree 形状与预期不符 —— 先看清楚再写，⛔ 不许盲改",
        )
    out: list[StackEntry] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise AssetError(
                f"{STACK_FIELD}[{index}] 不是 Mapping（读到 {type(entry).__name__}）",
                hint=f"这一项应当有 {GROUP_FIELD} / {ITEM_FIELD} 两个字符串（kb/18 §6.3）",
            )
        group = entry.get(GROUP_FIELD)
        item = entry.get(ITEM_FIELD)
        if not isinstance(group, str) or not isinstance(item, str):
            raise AssetError(
                f"{STACK_FIELD}[{index}] 的 {GROUP_FIELD} / {ITEM_FIELD} 不是字符串"
                f"（读到 {type(group).__name__} / {type(item).__name__}）",
                hint="名字规则不用我们猜（kb/18 §6.3）；类型不对说明读的不是这张表",
            )
        out.append(StackEntry(group=group, item=item))
    return tuple(out)


def set_stack(tree: Mapping[str, Any], group: str, item: str) -> dict[str, Any]:
    """**纯函数**：把 VT 栈那一项的两个**既有字段**改成新值（AGENTS §0.10 的「修改」）。

    ⛔ 只做「改值」，不做「增删栈项」：栈里不**恰好一项**就抛。
    这不是我们的选择，是这条链成立的前提 —— 项数一变长度必变（研究件把整表换成一项之后
    **等长**，说明真件本来就是一项），而本模块只做等长档（L1）。

    入参 `tree` **不被改**（拷一层再改），与 `writer.set_material_float` 同口径。
    """
    current = stack_entries(tree)
    if len(current) != 1:
        raise AssetError(
            f"{STACK_FIELD} 里有 {len(current)} 项（本模块只改**恰好一项**的那些材质）",
            hint="项数一变长度就变 ⇒ 不是等长档；多栈材质的口径没定下来之前 ⛔ 不许猜着改",
        )
    if not group or not item:
        raise AssetError(
            "group / item 不许为空",
            hint="两个值都是编辑器在构建期写进材质的名字（kb/18 §6.3）—— 照烘出来的抄",
        )
    new_tree = dict(tree)
    new_tree[STACK_FIELD] = [StackEntry(group=str(group), item=str(item)).to_tree()]
    return new_tree


# --------------------------------------------------------------------------- #
# ② / ③：等长 + 从新字节读回（自证用的两把尺子）
# --------------------------------------------------------------------------- #
def _readback_tree(obj: Any, data: bytes, read_tree: TreeReader | None) -> Mapping[str, Any]:
    """③ 用哪把尺子把**新字节**解回来。

    与已退役的 `mesh_write.rebuild_mesh_payload`（2026-10-25 随 mesh 栈删除）同口径
    （显式参数 → 对象自己 → 抛），三条：
    1. 调用方显式给了 `read_tree`；
    2. 对象自己实现了 `read_typetree_from_bytes(data)`（**离线夹具**的接缝）；
    3. 缺省走 `writer` 的那一把（真件：UnityPy 按同一个 typetree 反序列化）。
    """
    if read_tree is not None:
        return read_tree(obj, data)
    hook = getattr(obj, "read_typetree_from_bytes", None)
    if callable(hook):
        return hook(data)
    return writer._tree_from_bytes(obj, data)  # noqa: SLF001 -- 同一把尺子只有一份，不在这里抄第二份


def build_stack_change(
    source: Any,
    path_id: int,
    group: str,
    item: str,
    *,
    name: str = "",
    read_tree: TreeReader | None = None,
) -> writer.ObjectChange:
    """把「材质里的 VT 组指向」改成新值，产出**等长**的新对象字节。

    四条自证照 `writer.build_material_change` 的风格。

    `source` 是 `asset_kernel.unitypy_source.AssetSource`（要它已经打开 UnityPy 那一侧）。
    ① 不改动的 typetree 往返**逐字节相同**；② 改完**等长**；③ 从新字节**读回** group/item == 新值；
    ④ **新旧相同 ⇒ 抛错**（空改动不许写盘）。任何一条不成立都抛，⛔ 不许硬写。
    """
    obj = source.object_at(path_id)
    if obj is None:
        raise AssetError(
            f"这个包里没有 pathID={path_id} 的对象",
            hint="材质表与对象表对不上 —— 重开一次；⛔ 不要拿相近的 pathID 顶替",
        )
    original = bytes(obj.get_raw_data())
    tree = obj.read_typetree()
    if obj.save_typetree(tree) != original:
        raise AssetError(
            f"pathID={path_id} 的 typetree 往返不是逐字节相同",
            hint="这个对象的序列化不可复现 ⇒ 等长覆盖会改坏别的字段，本单不许写",
        )
    current = stack_entries(tree)
    if len(current) != 1:
        raise AssetError(
            f"pathID={path_id} 的 {STACK_FIELD} 有 {len(current)} 项（只改恰好一项的那些）",
            hint="项数一变长度就变 ⇒ 不是等长档；⛔ 不许猜着改",
        )
    old = current[0]
    target = StackEntry(group=str(group), item=str(item))
    if not target.group or not target.item:
        raise AssetError(
            "group / item 不许为空",
            hint="照编辑器烘出来写进材质的那两个名字抄（kb/18 §6.3）",
        )
    if old == target:
        raise AssetError(
            f"新旧相同（{old.group} / {old.item}）⇒ 空改动",
            hint="空改动不该写盘：换一个真烘出来的组，或者什么也别做",
        )

    payload = obj.save_typetree(set_stack(tree, target.group, target.item))
    if len(payload) != len(original):
        raise AssetError(
            f"改 VT 组指向之后对象长度 {len(original):,} → {len(payload):,} B（不是 L1）",
            hint="等长是这条链的前提；长度变了必须报错退出（⛔ 不许硬写）",
        )
    got = stack_entries(_readback_tree(obj, payload, read_tree))
    if got != (target,):
        raise AssetError(
            "写出来的字节读回去 "
            f"{[(entry.group, entry.item) for entry in got]}，期望 [{(target.group, target.item)}]",
            hint="序列化没把新值写进去 —— 这一版不许写回",
        )
    target_name = name or str(tree.get("m_Name") or f"<material {path_id}>")
    return writer.ObjectChange(
        path_id=path_id, payload=payload, name=target_name, type_name="Material"
    )


# --------------------------------------------------------------------------- #
# 池子就位情况（**只读**：⛔ 这里不搬文件）
# --------------------------------------------------------------------------- #
def streaming_dir_of(game_root: str | Path) -> Path:
    """游戏根 -> 池子该在的那个目录（`BrokenArrow_Data\\StreamingAssets`）。"""
    return Path(game_root).joinpath(*STREAMING_SUBDIR)


@dataclass(frozen=True, slots=True)
class PoolFile:
    """池子里的一个文件在游戏盘上的就位情况（`.gts` / `.gtp`）。"""

    name: str
    size: int
    installed_size: int | None = None

    @property
    def ok(self) -> bool:
        return self.installed_size == self.size

    @property
    def state(self) -> str:
        if self.installed_size is None:
            return "缺"
        if self.installed_size != self.size:
            return f"大小不符（盘上 {self.installed_size:,} B）"
        return "就位"


def pool_status(pool_dir: str | Path, streaming_dir: str | Path) -> tuple[PoolFile, ...]:
    """**只读**：池子目录里的每个文件，在 `StreamingAssets` 里有没有、大小对不对。

    ⛔ 这里**不搬文件**：`.gts/.gtp` 是「新增文件」（AGENTS §0.10），走的是另一条通道。
    本函数只回答一件事：**材质指过去之后，游戏有没有东西可读**——
    缺文件的池子会让材质指向一个加载不到的组（真机症状见 `kb/18` §6.2 的 `Could not load`）。
    """
    source = Path(pool_dir)
    if not source.is_dir():
        raise AssetError(
            f"池子目录不在了：{source}",
            hint="池子是「出 Player」烘出来的那 5 个文件（.gts + .gtp）—— 先烘再指",
        )
    target = Path(streaming_dir)
    files = sorted(path for path in source.iterdir() if path.is_file())
    if not files:
        raise AssetError(
            f"池子目录里一个文件都没有：{source}",
            hint=".gts / .gtp 一个不落地列出来才算池子",
        )
    out: list[PoolFile] = []
    for path in files:
        installed = target / path.name
        out.append(
            PoolFile(
                name=path.name,
                size=path.stat().st_size,
                installed_size=installed.stat().st_size if installed.is_file() else None,
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------- #
# 读侧：unit_key -> 唯一命中包 -> 材质 -> 改动的干跑计划
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class VtStackPlan:
    """一次「修改 VT 组指向」的干跑结果（⛔ 一个字节都没写）。"""

    game_root: Path
    unit_key: str
    bundle: Path
    bundle_name: str
    catalog_path: Path
    material_name: str
    material_path_id: int
    material_bytes: int
    old_group: str
    old_item: str
    new_group: str
    new_item: str
    #: 等长的新对象字节（writer.ObjectChange）—— install 就把它交给 writer。
    change: writer.ObjectChange
    #: writer 的干跑计划（对象字节数 原/新/级别 + 命中块 + catalog CRC 全在里面）。
    writeback: writer.WritebackPlan
    #: 新旧对象字节的差异段（对象内相对偏移）。
    changed_spans: tuple[tuple[int, int], ...] = ()
    #: 目标材质的**来路**（`prefab 渲染器` / `皮肤字典 Skins.Data[i].Data._keyData[j]`）
    #: —— 2026-10-11 加（只改 prefab 那几颗时，切涂装会被皮肤字典里的同族顶掉）
    material_sources: tuple[str, ...] = ()
    #: **同族**材质名（同一个皮肤字典里、同一组渲染器上的其它颗）—— 会顶掉这一颗的就是它们
    material_kin: tuple[str, ...] = ()

    @property
    def changed_bytes(self) -> int:
        return sum(end - start for start, end in self.changed_spans)

    def object_stream_start(self) -> int | None:
        """目标对象在**解压流**里的绝对起点（writer 那一行的 stream_start）。"""
        for row in self.writeback.rows:
            if row.path_id == self.material_path_id:
                return int(row.stream_start)
        return None

    def absolute_spans(self) -> tuple[tuple[int, int], ...]:
        """改动在解压流里的绝对区间。"""
        start = self.object_stream_start()
        if start is None:
            return ()
        return tuple((start + lo, start + hi) for lo, hi in self.changed_spans)

    def criterion_lines(self) -> tuple[str, ...]:
        """**判据一行**：包名 / 材质 / 旧→新两个名字 / 改动字节数 / 对象字节等长。

        后面接 writer 那几行（命中块 / 预期文件大小 / catalog）。
        """
        head = (
            f"[修改 VT 组指向] {self.bundle_name}（{self.unit_key}）："
            f"材质『{self.material_name}』 pathID={self.material_path_id}  "
            f"{STACK_FIELD}：{self.old_group} / {self.old_item} → "
            f"{self.new_group} / {self.new_item}"
        )
        spans = self.absolute_spans()
        where = "、".join(f"abs {lo:,}~{hi:,}" for lo, hi in spans) or "（无差异）"
        origin = " ＋ ".join(self.material_sources or ("prefab 渲染器",))
        if self.material_kin:
            kin = (f"同族 {len(self.material_kin)} 颗：" + " / ".join(self.material_kin)
                   + "（切到别的涂装时它们会顶掉这一颗）")
        else:
            kin = "同族 0 颗（这个皮肤字典里没有别的材质）"
        return (
            head,
            f"  材质来路：{origin} ｜ {kin}",
            f"  改动 {self.changed_bytes} 字节（{len(spans)} 段）：{where}"
            f"   对象字节 {self.material_bytes:,} B 原样长度（L1 等长）",
            *self.writeback.criterion_lines(),
        )

    def summary(self) -> str:
        return (
            f"{self.bundle_name}：修改材质 {self.material_name} 的 VT 组指向"
            f"（{self.old_group[:8]}… / {self.old_item[:8]}… → "
            f"{self.new_group[:8]}… / {self.new_item[:8]}…，{self.changed_bytes} 字节）"
        )


def plan_install(
    game_root: str | Path,
    unit_key: str,
    *,
    material_name: str,
    group: str,
    item: str,
) -> VtStackPlan:
    """**干跑**：算出「改哪几个字节」+ writer 那条写回计划的真值（⛔ 只读、不写、不建补丁）。

    链接：unit_key -> catalog -> 唯一命中包 -> prefab 的材质 -> 栈表 -> 改两个字段
    -> `build_stack_change`（四条自证）-> `writer.write_objects(dry_run=True)`（命中块 / 双 CRC /
    catalog 平移全算出来）。读源在 `with` 里关掉之后才进 writer（Windows 上还有句柄时换不了文件）。
    """
    root = Path(game_root)
    with skin_install._open_unit(root, unit_key) as unit:  # noqa: SLF001 -- 见模块头「私有件复用」
        materials = skin_install._materials_of(unit)  # noqa: SLF001 -- 同上
        material_id = skin_install._find_material(  # noqa: SLF001 -- 同上
            materials, material_name, unit_key
        )
        # 来路与同族：材质表里那几个注释键（skin_install._materials_of 填的，见那里的文档）
        record = materials.get(material_id) or {}
        material_sources = tuple(str(one) for one in (record.get("sources") or ()))
        material_kin = tuple(str(one) for one in (record.get("kin") or ()))
        tree = unit.source.read_typetree(material_id)
        current = stack_entries(tree)
        if len(current) != 1:
            raise AssetError(
                f"{unit_key} 的材质『{material_name}』里 {STACK_FIELD} 有 {len(current)} 项"
                "（本模块只改恰好一项的那些）",
                hint=f"读到的是 {[(e.group, e.item) for e in current]}"
                " —— 多栈材质的口径没定下来，⛔ 不许猜",
            )
        obj = unit.source.object_at(material_id)
        if obj is None:
            raise AssetError(
                f"这个包里没有 pathID={material_id} 的对象",
                hint="材质表与对象表对不上 —— 重开一次；⛔ 不要拿相近的 pathID 顶替",
            )
        old_bytes = bytes(obj.get_raw_data())
        change = build_stack_change(unit.source, material_id, group, item, name=material_name)
        bundle, bundle_name, catalog_path = unit.bundle, unit.bundle_name, unit.catalog
    spans = skin_install.changed_spans(old_bytes, change.payload)
    report = writer.write_objects(
        bundle, [change], dry_run=True, game_root=root, catalog_path=catalog_path
    )
    return VtStackPlan(
        game_root=root,
        unit_key=str(unit_key),
        bundle=Path(bundle),
        bundle_name=str(bundle_name),
        catalog_path=Path(catalog_path),
        material_name=str(material_name),
        material_path_id=int(material_id),
        material_bytes=len(old_bytes),
        old_group=current[0].group,
        old_item=current[0].item,
        new_group=str(group),
        new_item=str(item),
        change=change,
        writeback=report.plan,
        changed_spans=spans,
        material_sources=material_sources,
        material_kin=material_kin,
    )


def install(
    plan: VtStackPlan, *, project_root: str | Path, dry_run: bool = False
) -> writer.WriteReport:
    """执行一次安装（`dry_run=True` 只算不写）。形状照 `skin_install.install_swap`。

    * 写回那一趟全在 `writer.write_objects` 里：等长覆盖 + 重算 CRC + **成对**更新 catalog；
    * `patch_store` 给了就走 ADR 0007 的反向 Δ（⛔ **不拷整包**）—— 补丁落
      `<工程目录>/patches/`（PatchStore 自带同盘守卫），还原走 `assets restore`；
    * ⛔ 游戏在跑会被 writer 拦下（`is_game_running`），这里不再另写一份。
    """
    store = reverse_delta.PatchStore(
        reverse_delta.patch_store_root(project_root), game_root=plan.game_root
    )
    return writer.write_objects(
        plan.bundle,
        [plan.change],
        dry_run=dry_run,
        project_root=Path(project_root),
        game_root=plan.game_root,
        catalog_path=plan.catalog_path,
        patch_store=store,
        patch_label=(
            f"VT 组指向：{plan.unit_key} {plan.material_name}，"
            f"{plan.old_group}/{plan.old_item} → {plan.new_group}/{plan.new_item}"
        ),
    )
