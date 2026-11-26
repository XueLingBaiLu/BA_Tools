"""把一辆游戏单位导进 Blender（W3 只读导入）。

## 口径（照 W2 的契约，⛔ 不另写一份换算）

- 坐标：位置 / 四元数 / 缩放**一律**走 asset_kernel.coords（unity_to_blender 一族），
  父链交给 Blender 自己复合 —— 本模块里**没有**任何手写的换轴算式（工单 §0 的口径）；
- 颜色空间：**逐张**走 asset_kernel.colorspace.apply_colorspace，不靠「新建图像默认 sRGB」；
- 顶点流 / 流式字节：走 asset_kernel.mesh + .streaming（.resS 的定位只有那一份实现）；
- 材质：一个材质 -> 一个 MaterialPlan（asset_kernel/materials.py）-> 四个角色
  （base_color / normal / mask / emission）各取一张图 -> blender_addon/materials.py
  照 NODE_SHAPE 建节点树；法线走 DXT5nm -> 派生 RGB 法线图（z 只由内核那一份公式算）。

## 可瞄武器的静止朝向：★ 2026-10-15 加了一次**校正**（默认开、可关）

实测 RU_T72B 炮塔件：那挺 `Kord_turret` 在 prefab 里被一个 `localRot=(0,-1,0,0)`（绕 +Y 180°）
的转台 `turret_0_0` 摆到**车尾**，而主炮 `weapon_0_1`（同一个爹）是单位四元数、朝车头。
⇒ **是资产自己的静止姿势，不是导入换轴翻的**（整棵 Base 除 `exhaust` 外全是单位四元数）。
默认按 `align_aimable=True` 把这类转台绕垂直轴转 180° 对齐车头 ——
⛔ **这是校正，不是 prefab 原样**：读数在 `ImportReport.aim_*`（`summary()` 里也会写一句），
规则与两道安全闸见 `blender_addon/rig_plan.py` 末尾那一节；要原样传 `align_aimable=False`。

## 层级 / LOD 的口径（本单实测；与工单 §2③「默认只导 LOD0」的口径冲突也记在这里）

实测 RU_T72B_Base：52 节点 / renderer **13** / 材质 **3** / 贴图 **3**，
LODGroup 4 级，LOD0 = 7 个，**四级并集 = 13**（LOD1 与 LOD0 共用 T72BA/K1/K5）。

于是：**默认导全部 13 个 renderer**（只导 7 个时，材质是 2、贴图 3，与工单 §3 D1 的
「13 / 3 / 3」对不上）。LOD1/2/3 那 6 个对象默认 hide_render —— 四个级别叠在一起渲
是错的，游戏同一时刻只显示一级。
only_lod0=True 时只建 LOD0 的 7 个（那时 7 / 2 / 3，用例里也钉住两个读数）。

lod_1x1x1 的网格是**外部引用**（PPtr m_FileID=2 = "unity default resources"，本包没有
那个网格）⇒ 建空物体占位 + 把它的材质建出来，并记进 report.unresolved（不许静默丢）。

★ 2026-10-15 加了**文件路**：给一个磁盘上的 `.bundle` 文件直接拿件（不走 catalog）——
口径表与四个入口见下面「# ★ 2026-10-15 · 直接从磁盘上一个 .bundle 拿件」那一节
（`check_bundle_file` / `plan_bundle_take` / `import_bundle` / `bundle_summary_line`）。
两条路共用 `_import_from_source`（建场景只有一份实现）。
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

import bpy

from ba_mod_maker.asset_kernel import coords, skeleton, unitypy_source
from ba_mod_maker.asset_kernel import prefab as prefab_mod
from ba_mod_maker.asset_kernel.colorspace import apply_colorspace, colorspace_of
from ba_mod_maker.asset_kernel.materials import (
    ROLE_BASE_COLOR,
    ROLE_CAMO,
    ROLE_EMISSION,
    ROLE_IRIDESCENCE,
    ROLE_MASK,
    ROLE_NORMAL,
    MaterialPlan,
    plan_material,
    plan_unit,
)
from ba_mod_maker.asset_kernel.mesh import MeshData
from ba_mod_maker.core.assets.catalog import AddressablesCatalog

from . import armature as blender_armature
from . import materials as blender_materials
from . import rig_plan, unit_graph, vt_cache
from . import session as blender_session

__all__ = [
    "ImportReport",
    "UnitLocation",
    "import_unit",
    "list_units",
    "catalog_has_address",
    "locate_unit",
    # ★ 2026-10-15 · 文件路（不走 catalog）
    "BundleFileError",
    "BundleListing",
    "BundlePiece",
    "apply_bundle_take",
    "bundle_signature",
    "bundle_summary_line",
    "check_bundle_file",
    "import_bundle",
    "list_bundle",
    "list_bundle_lines",
    "open_bundle_file",
    "plan_bundle_take",
]


@dataclass(slots=True)
class ImportReport:
    """一次导入的读数（每条都能在 Blender 里复核）。"""

    unit_key: str = ""
    asset_path: str = ""
    bundle: str = ""
    #: ★ 2026-10-15：这个包在**磁盘上**的真实路径。
    #:   catalog 路 = `<游戏根>/…/aa/PC/<包名>`；文件路 = 用户给的那个 `.bundle` 文件。
    #:   「② 改」写回时按**这个**开包 —— 文件路取来的件可能根本不在 aa/PC 里。
    bundle_path: str = ""
    candidates: tuple[str, ...] = ()
    only_lod0: bool = False
    nodes: int = 0
    non_renderer_objects: int = 0
    renderers: int = 0
    mesh_objects: int = 0
    placeholder_objects: int = 0
    materials: int = 0
    #: ★ 2026-10-18：这个数是**图上的**（目标材质引用到的贴图去重数，
    #: 由 `unit_graph.counts_of` 说）。
    textures: int = 0
    #: 这次**真解出来并建进 `bpy.data.images`** 的张数（真实读数）。
    #: 两者不等 ⇒ 有图没解出来（`unresolved` 里逐个点名）—— ⛔ 不许拿引用数冒充落地数。
    textures_decoded: int = 0
    lod_levels: int = 0
    lod0_members: int = 0
    lod_union: int = 0
    #: 不在任何 LOD 级里的 renderer 数（一律可见，不算 LOD 变体）。
    lod_outside: int = 0
    lod_hidden: int = 0
    bones_matched: int = 0
    bones_unmatched: int = 0
    bone_empty_slots: int = 0
    #: ★ 补骨架（2026-10-12，设计稿 §3「蒙皮」那一格）：骨架对象名 / 骨数 / 读数。
    #: ⛔ 之前这里什么都没有 —— 顶点组在、骨架不在，用户按 G 拖 root/body **车纹丝不动**。
    armature: str = ""
    bones: int = 0
    bones_deform: int = 0
    bone_names: tuple[str, ...] = ()
    rig_summary: str = ""
    rig_issues: tuple[str, ...] = ()
    #: 真挂上 Armature 修改器的对象名（复算：这些对象的 modifiers 里有 ARMATURE）。
    armature_modified: tuple[str, ...] = ()
    #: rest 姿势下「求值网格 vs 原始顶点」的最大位移（米）—— **必须 ~0**：
    #: 补骨架不许把模型挪位（尺子 = armature.rest_max_delta）。
    armature_rest_delta: float = 0.0
    #: 骨架没建起来时的一句人话（⛔ 不静默：它进 summary()，用户当场看得见）。
    rig_error: str = ""
    #: ★ 2026-10-15 · 可瞄武器朝向校正（`rig_plan.align_aim_nodes`）的读数。
    #: ⛔ 这一组存在的唯一目的：**不许把「校正」写成像「原样」** —— 用户看到的车
    #: 是被绕过 180° 的（资产静止姿势把机枪摆在车尾）。要原样请给 `align_aimable=False`。
    aim_summary: str = ""
    #: 认出来的「武器转台」名（含**没转**的那些 —— 要看得见它们为什么不动）。
    aim_mounts: tuple[str, ...] = ()
    #: 真被转了 180° 的转台名。
    aim_rearward: tuple[str, ...] = ()
    #: 逐件一行（面板「原始读数」与探针用）。
    aim_lines: tuple[str, ...] = ()
    #: 安全闸的读数（⛔ 不静默吞）。
    aim_issues: tuple[str, ...] = ()
    object_of: dict[int, str] = field(default_factory=dict)
    #: ★ 2026-10-15：**对象名 -> 它那段网格在包里的 pathID**。「② 改 · 这个件」那一格要用它 ——
    #:   场景里那颗网格对象 ⇄ 包里那个 Mesh 就是靠这张表配上的（⛔ 不靠猜名字 / 不靠序号）。
    mesh_of: dict[str, dict] = field(default_factory=dict)
    #: ★ 2026-10-15：**对象名 -> 它那个节点的 Transform pathID**。「② 改」的**变换段**要用它
    #:   （挪整件 = 写 `m_LocalPosition` / `m_LocalRotation` / `m_LocalScale`）。
    #:   ⚠ 空物体（分组节点）也记 —— 挪父节点会带着子节点一起走。
    transform_of: dict[str, int] = field(default_factory=dict)
    #: 这一次导入落进哪个集合（改 / 验 / 一键还原都按「一个件」把它找回来）。
    collection_name: str = ""
    #: transform_id -> **Unity 侧**世界坐标（父链复合的结果）。导入后拿它跟 Blender
    #: 自己算出来的 matrix_world 比 —— 两条机制不同的路径，对上才叫 D2 通过。
    world_of: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    image_colorspace: dict[str, str] = field(default_factory=dict)
    material_names: tuple[str, ...] = ()
    #: W4：逐材质一行（形状冻结，见 _material_row）。值一律 JSON 友好（list，不是 tuple/set）。
    material_rows: tuple[dict[str, object], ...] = ()
    #: ★ 2026-10-15：① 拿 时**自动接 VT 法线 / 遮罩**的逐材质读数（面板「看详情」）。
    #:   ⛔ 每种结局都要显形（接上 / 自带法线 ⇒ 不动 / 没图 ⇒ 不接 / 不是 VT ⇒ 不动），
    #:   不许静默跳过。读数的形状见 blender_addon.materials.VtReadout.line()。
    vt_map_lines: tuple[str, ...] = ()
    #: 真接上 VT 图的材质数（= vt_map_lines 里「接上」那些的条数）。
    vt_map_bound: int = 0
    #: ★ 2026-10-17：LOD 档尺寸离群被归一过的读数（一行一档；空 = 没动）。
    lod_size_fixes: tuple[str, ...] = ()
    #: 这一次没图时的那句人话（点名缺哪张 / 哪个目录）；有图就是空串。
    vt_map_note: str = ""
    #: W4：逐条可显示的「未还原」行 = RuntimeItem.line()（UnitPlan.runtime_items）。
    material_unreduced: tuple[str, ...] = ()
    #: W4：= len(material_unreduced)。
    material_runtime_count: int = 0
    node_names: tuple[str, ...] = ()
    #: 与 node_names 同序的 transform_id 与**从 prefab 根起的层级路径** —— Blender 不许
    #: 重名（Unity 允许），所以 D5 要比路径、并把被自动改名的节点逐条报出来。
    node_order: tuple[int, ...] = ()
    node_paths: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    seconds: float = 0.0

    @property
    def created_objects(self) -> int:
        return self.renderers + self.non_renderer_objects

    def summary(self) -> str:
        rig = ""
        if self.rig_summary:
            rig = (
                f" / {self.rig_summary} / 挂修改器 {len(self.armature_modified)} 件"
                f"（rest 位移 {self.armature_rest_delta:.2e} m）"
            )
        elif self.rig_error:
            rig = f" / ⚠ 骨架没建起来：{self.rig_error}"
        # ★ 2026-10-15：真的转过 180° 时才写这一句 —— 而且**必须**把它写成「校正」。
        aim = f" / {self.aim_summary}" if self.aim_rearward else ""
        # ★ 2026-10-15：VT 图那一段只在**真接上**时写进摘要 —— 没图 / 一颗都不该接时，
        #   原因在 vt_map_lines / vt_map_note 里逐条点名（⛔ 摘要不许塞一句含糊的话）。
        vt = f" / VT 图 {self.vt_map_bound} 颗" if self.vt_map_bound else ""
        return (
            f"{self.unit_key}: 节点 {self.nodes} / renderer {self.renderers}"
            f"（网格 {self.mesh_objects} + 占位 {self.placeholder_objects}）"
            f" / 材质 {self.materials} / 贴图 {self.textures}"
            f" / LOD {self.lod_levels} 级（LOD0 {self.lod0_members}，并集 {self.lod_union}，"
            f"不渲染 {self.lod_hidden}）" + rig + aim + vt + f" / {self.seconds:.1f}s"
        )


@dataclass(frozen=True, slots=True)
class UnitLocation:
    """地址键解析出来的位置（catalog + 候选包；**不开包**）。"""

    unit_key: str
    asset_path: str
    candidates: tuple[str, ...]


def aa_dir_of(game_root: str | Path) -> Path:
    return Path(game_root) / "BrokenArrow_Data" / "StreamingAssets" / "aa"


def catalog_path_of(game_root: str | Path) -> Path | None:
    """catalog 文件在哪 —— **两个名字都认**（`catalog.json` ≤1.2.0 / `catalog.bin` ≥1.2.1）。

    ★ 2026-10-15 补：本模块原来写死 `catalog.json`，而 1.2.1 起游戏换成了 `catalog.bin`
    （口径见 `core/assets/game.py::CATALOG_NAMES` 与 `core/assets/catalog.py::load`）——
    照旧写死的话，`list_units` 在这台机器上**一定**抛 FileNotFoundError。
    """
    from ba_mod_maker.core.assets.game import CATALOG_NAMES

    aa_dir = aa_dir_of(game_root)
    for name in CATALOG_NAMES:
        candidate = aa_dir / name
        if candidate.is_file():
            return candidate
    return None


def locate_unit(game_root: str | Path, unit_key: str) -> UnitLocation:
    """地址键 -> (资产路径, 候选包)。只查 catalog，不开包（开包很贵）。"""
    aa_dir = aa_dir_of(game_root)
    catalog = AddressablesCatalog.load(aa_dir)
    if catalog is None:
        raise FileNotFoundError(f"读不到 catalog：{aa_dir}（catalog.json / catalog.bin 都没有）")
    hits = catalog.lookups(unit_key)
    if not hits:
        raise KeyError(f"catalog 里没有地址键 {unit_key!r}（共 {catalog.key_count} 个键）")
    assets = sorted({asset for asset, _bundle in hits})
    if len(assets) != 1:
        raise ValueError(f"地址键 {unit_key!r} 指向 {len(assets)} 个资产：{assets}")
    # ★ 2026-10-20（用户需求：**要从我们新造的包里提取模型**）：catalog 的包字段
    #   可能是我们装机写下的 `..\..\..\BA_Mod\<包>`，也可能只是裸文件名（原版）。
    #   这里统一解析成**盘上真路径**（`resolve_bundle_path`）；`open_unique_bundle` 里
    #   `base / 绝对路径` 会取绝对路径 ⇒ 两条都能开。⛔ 解析不到就退回原名（让上层报错）。
    from ba_mod_maker.core.assets.game import original_bundle_name, resolve_bundle_path

    resolved: list[str] = []
    for _asset, bundle in hits:
        if not str(bundle or "").strip():
            continue
        found = resolve_bundle_path(Path(game_root), bundle)
        resolved.append(str(found) if found is not None else original_bundle_name(bundle))
    bundles = tuple(sorted(set(resolved)))
    return UnitLocation(unit_key=unit_key, asset_path=assets[0], candidates=bundles)


#: Addressables 给同一条地址还会挂一个 32 位十六进制哈希键（实测），那不是"名字"。
_HASH_KEY = re.compile(r"^[0-9a-fA-F]{32}$")


@lru_cache(maxsize=4)
def _unit_keys_cached(aa_dir: str, _mtime: float) -> tuple[str, ...]:
    catalog = AddressablesCatalog.load(Path(aa_dir))
    if catalog is None:
        return ()
    keys: list[str] = []
    for key in catalog.index:
        found = catalog.lookups(key)
        if not found:
            continue
        asset = found[0][0].replace("\\", "/")
        if not (asset.endswith(".prefab") and "/ModelPrefabs/" in asset):
            continue
        # 只留"像名字"的键：裸名、且不是那条 32 位哈希别名。
        if "/" in key or "\\" in key or _HASH_KEY.match(key):
            continue
        keys.append(key)
    return tuple(sorted(keys))


def list_units(game_root: str | Path) -> tuple[str, ...]:
    """catalog 里所有指向 ModelPrefabs prefab 的**裸名地址键**（= Units.ModelFileName 的原样值）。

    ⚠ 口径要说清：这一层只有 catalog（地址 -> 资产），**没有 24 张表**，所以它分不出
    "单位 / 武器 / 弹药"。实测 28,904 个键里：

    - 指向 /ModelPrefabs/*.prefab 的键 **3,348** 个（含 32 位哈希别名）；
    - 其中**裸名且不是哈希**的 **913** 个（单位 + 武器 + 弹药），RU_T72B 在里面。

    要精确到设计稿 §6 第 2 步那个「**335 个单位**」，得读 Units.ModelFileName
    （那是产品数据侧的活，本单不碰数据库）。
    """
    aa_dir = aa_dir_of(game_root)
    catalog_path = catalog_path_of(game_root)
    if catalog_path is None:
        raise FileNotFoundError(f"读不到 catalog：{aa_dir}（catalog.json / catalog.bin 都没有）")
    return _unit_keys_cached(str(aa_dir), catalog_path.stat().st_mtime)


@lru_cache(maxsize=4)
def _catalog_keys_cached(aa_dir: str, _mtime: float) -> frozenset[str]:
    """catalog 里全部地址键（与 _unit_keys_cached 同一把缓存口径：aa_dir + mtime）。"""
    catalog = AddressablesCatalog.load(Path(aa_dir))
    return frozenset(catalog.index) if catalog is not None else frozenset()


def catalog_has_address(game_root: str | Path, address: str) -> bool:
    """这条地址在 catalog 里**已经有了**吗（⑦ 面板「撞名当场给话」用）。

    读不动 catalog（没填游戏根 / 两个名字都没有）⇒ False —— 读不到就不吓唬用户。
    """
    text = str(address or "").strip()
    if not text:
        return False
    aa_dir = aa_dir_of(game_root)
    catalog_path = catalog_path_of(game_root)
    if catalog_path is None:
        return False
    return text in _catalog_keys_cached(str(aa_dir), catalog_path.stat().st_mtime)


def _material_row(plan: MaterialPlan) -> dict[str, object]:
    """一行材质报告（**形状冻结**，② 材质面板按它画）。值一律 JSON 友好（不做 tuple/set）。"""
    return {
        "material": plan.material_name,
        "shader": plan.shader,
        "table": plan.table_key,
        "coverage": plan.coverage,
        "alpha_mode": plan.alpha_mode,
        "runtime_items": [item.key for item in plan.runtime_items],
        "unmapped_slots": list(plan.unmapped_slots),
        "notes": list(plan.notes),
    }


def _material_prop_descs(source, material_id: int) -> dict:
    """这颗材质的 shader **自写 desc**（颜色层就写在里面）—— 读不到给空 dict。

    ★ 2026-10-15（viewport-look 真机量到的「一块平的暗面」真凶）：DLC 车那批走 UnitV4_Camo，
    颜色层**只在 desc 里**；不读它 ⇒ 拿进来的车体材质一路都不接（灰面）。
    实测 RU_BMD_Kornet：读了之后「接上的材质 1/4 → 3/4 · 有图的节点 3 → 5」，
    同机位渲染像素差 mean=0.0872 max=0.8784 changed=20.8%。
    ⛔ 读不到就如实不接（调用方传 prop_descs=None）—— 不许编一个默认值。
    """
    try:
        return dict(blender_materials.shader_prop_descs(source, int(material_id)) or {})
    except Exception:  # noqa: BLE001 —— ⛔ 读不到 desc 不许把「拿件」整条路弄崩
        return {}


def _drop_failed_normal(plan: MaterialPlan, images: Mapping[int, str]) -> MaterialPlan:
    """法线贴图解码失败（已记进 report.errors）⇒ 摘掉那条连线，不废掉整次导入。

    ⚠ 这是**唯一**允许「不接法线」的路径：调用方忘了烘派生图时，
    blender_addon.materials.build_material_from_plan 仍然会 raise ValueError（两条路不许混）。
    """
    slot = plan.slot_of(ROLE_NORMAL)
    if slot is None or slot.texture_id is None or int(slot.texture_id) in images:
        return plan
    if not any(link.role == ROLE_NORMAL and link.wired for link in plan.links):
        return plan
    note = f"法线贴图 pathID={slot.texture_id} 解码失败 ⇒ 该材质不接法线（见 report.errors）"
    return replace(
        plan,
        links=tuple(link for link in plan.links if link.role != ROLE_NORMAL),
        notes=(*plan.notes, note),
    )


def _material_images(
    source: unitypy_source.AssetSource,
    plan: MaterialPlan,
    unit_key: str,
    decoded: dict[int, tuple[str, bytes, int, int]],
    image_of: dict[int, str],
    report: ImportReport,
) -> tuple[dict[int, str], dict[int, str]]:
    """四个角色各取一张图。

    :returns: (images: pathID→Blender 图名, normal_source: pathID→原 DXT5nm 图名)。
        normal 角色给的是**派生图**（bake_normal_image 烘的 RGB 法线），不是原 DXT5nm 图。

    - 角色没赋图（slot_of 返回 None 或 texture_id 为 None）→ 跳过，不报错；
    - 解码失败 → 记进 report.errors 后跳过（W3 的兜底，不废整次导入）。
    """
    images: dict[int, str] = {}
    normal_source: dict[int, str] = {}
    # ★ 2026-10-17：虹彩厚度层也要解（68 颗 HDRP/Lit 绑了它；进节点前由
    #   `materials._apply_iridescence` 过 remap 再 ×1000 变纳米）。
    # ★ 2026-10-17：涂装层（desc='Camo'）也要解 —— 皮肤 1/2/3 的区别只在这张图。
    for role in (
        ROLE_BASE_COLOR,
        ROLE_CAMO,
        ROLE_EMISSION,
        ROLE_MASK,
        ROLE_NORMAL,
        ROLE_IRIDESCENCE,
    ):
        slot = plan.slot_of(role)
        if slot is None or slot.texture_id is None:
            continue
        texture_id = int(slot.texture_id)
        entry = _decode_texture(source, texture_id, unit_key, decoded, report)
        if entry is None:
            continue
        image_name, rgba, width, height = entry
        image_of.setdefault(texture_id, image_name)
        if role == ROLE_NORMAL:
            baked = blender_materials.bake_normal_image(texture_id, image_name, rgba, width, height)
            images[texture_id] = baked.name
            normal_source[texture_id] = image_name
        else:
            images[texture_id] = image_name
    return images, normal_source


def _bind_vt_maps(
    materials: list[bpy.types.Material],
    stacks_of: dict[str, tuple[str, str]],
    report: ImportReport,
    *,
    game_root: str | Path | None = None,
) -> None:
    r"""① 拿 时把 VT 的 Normal / Mask **自动**接上（就地填读数，⛔ 不静默）。

    ★ 2026-10-17 重写（用户点名两次：**发布出去不能靠开发机目录** / **别人换别的模型也得能抽**）：

    1. **图从游戏原文件现抽**，落进**用户缓存**
       `%LOCALAPPDATA%\BAModMaker\vt\<group>\<item>_{0,1}.png`
       （`blender_addon.vt_cache`；纯标准库，走 `ba_mod_maker.asset_kernel.granite_vt`）。
       旧版读的是开发机上的 `_work/vt-extract/` —— 发布出去那个目录根本不存在。
    2. **按每颗材质自己的 (group, item) 取图，不按单位取**：旧版把**同一对** unit 级图绑给
       该次导入的**每一颗**材质 ⇒ 多材质单位（RU_BMD_Kornet 4 颗 / M4 实测 57 颗）
       **除第一颗外全部接错**，而且静默（不判红）。

    逐材质的判断仍然只有一份：`blender_addon.materials.vt_decisions`。
    ⛔ 抽不到时**一个字节都不写**：只出读数 ＋ 一句点名（池在哪 / 为什么抽不出来）。
    """
    if not materials:
        return
    # ★ 2026-10-17：**按每颗材质自己的 (group, item)** 取图，不再按单位取。
    rows: list = []
    notes: list[str] = []
    for material in materials:
        stack = stacks_of.get(material.name)
        if not stack:
            notes.append(f"{material.name}：没有 VT 栈（材质上没有 m_BuildTextureStacks）⇒ 不接")
            continue
        paths, note = vt_cache.ensure_maps(stack[0], stack[1], game_root=game_root)
        rows.extend(
            blender_materials.vt_decisions(
                [material], blender_materials.role_maps(paths), missing=note
            )
        )
    report.vt_map_lines = tuple([row.line() for row in rows] + notes)
    report.vt_map_bound = sum(1 for row in rows if row.bound)
    report.vt_map_note = "；".join(notes)


#: ⛔ 2026-10-17 用户裁定：**撤回 LOD 尺寸归一**。
#: 原话：「我还是撤回对插件的改动吧，这个工作残留正好能让我学习游戏模型相关的知识」——
#: 也就是说 AH-1W 那台 686 m 的巨物**故意留着**（它是「节点缩放 vs 骨骼驱动」的活教材）。
#: 代码不删：要恢复就把这里改成 True。
_LOD_SIZE_NORMALISE = False


def _union_world_size(objs: list) -> float:
    """这一组对象在世界坐标下的并集包围盒的**最大轴**（米）。

    ⚠ `mathutils` **只在本函数里 import**：那是真 Blender 才有的模块，而本模块要在
      venv 里被影子桩 import（`tests/test_uv1_camo.py` / `test_packed_maps.py` 都这么干）——
      模块级 `from mathutils import Vector` 会让「`import blender_addon.importer`」直接
      ModuleNotFoundError（实测：三份用例整片红，而它们量的是网格 / UV，与 mathutils 无关）。
    """
    from mathutils import Vector

    if not objs:
        return 0.0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for obj in objs:
        for corner in obj.bound_box:
            p = obj.matrix_world @ Vector(corner)
            for k in range(3):
                lo[k] = min(lo[k], p[k])
                hi[k] = max(hi[k], p[k])
    return max(hi[k] - lo[k] for k in range(3))


def _world_center(obj):
    """对象几何在**世界坐标**下的包围盒中心（`mathutils` 同上，只在本函数里 import）。"""
    from mathutils import Vector

    m = obj.matrix_world
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for corner in obj.bound_box:
        p = m @ Vector(corner)
        for i in range(3):
            lo[i] = min(lo[i], p[i])
            hi[i] = max(hi[i], p[i])
    return Vector(tuple((lo[i] + hi[i]) * 0.5 for i in range(3)))


def _scale_about_center(obj, k: float) -> None:
    """把对象缩放 ×k，**并把几何钉在原地**。

    ⛔ 只改缩放会把几何朝**对象原点**塌下去 —— 实测症状「位置偏了」（2026-10-17，用户真机）。
    """
    before = _world_center(obj)
    obj.scale = tuple(v * k for v in obj.scale)
    bpy.context.view_layer.update()
    after = _world_center(obj)
    matrix = obj.matrix_world.copy()
    matrix.translation = matrix.translation + (before - after)
    obj.matrix_world = matrix


def _normalise_lod_sizes(lod_levels, object_of_cid: dict) -> list[str]:
    """★ 2026-10-17：同一 LOD 链上某一档物理尺寸离群 ⇒ 把那一档的对象缩放归一。

    由来（用户真机 + 手改验过）：`US_AH-1W Super_Cobra` 的 LOD0 网格是**米制**（17.59 m），
    而 LOD1/LOD2 的网格是 **1/39.028 单位**（0.451 m）；美术靠节点缩放
    `Chassis 39.028397 × (POLY FEW) 0.025622 × LOD 子节点 39.028397` 把两边缝在一起。
    游戏里蒙皮件由骨骼驱动、那层缩放是死数据 ⇒ 没问题；但**按节点 TRS 摆的导入器**
    会把 LOD0 算成 686 m。

    ⛔ 这里**不改网格、不改 prefab**：只把离群那一档的**对象缩放**归一，让三档在 Blender 里
    一样大。判据是读数（并集包围盒最大轴 / 档间中位数），与名字无关。
    """
    if not lod_levels:
        return []
    sizes = []
    for level in lod_levels:
        objs = [
            o
            for o in (bpy.data.objects.get(object_of_cid.get(int(c), "")) for c in level)
            if o is not None and o.type == "MESH"
        ]
        if objs:
            sizes.append((objs, _union_world_size(objs)))
    if len(sizes) < 2:
        return []
    dims = sorted(size for _objs, size in sizes if size > 0)
    if not dims:
        return []
    median = dims[len(dims) // 2]
    lines: list[str] = []
    for objs, size in sizes:
        if size <= 0 or median <= 0:
            continue
        ratio = size / median
        if 0.5 <= ratio <= 2.0:
            continue
        k = 1.0 / ratio
        for obj in objs:
            _scale_about_center(obj, k)
        lines.append(
            f"LOD 档尺寸离群：{len(objs)} 件 {size:.4f} m vs 中位 {median:.4f} m"
            f"（×{ratio:.4f}）⇒ 缩放归一 ×{k:.6f}"
        )
    return lines


#: `ImportReport` 里**从图上导出**的那些计数字段 —— M1 的判据就是「这些数由 `unit_graph` 说」，
#: ⛔ 不再由建场景那一段各记一套（两套一漂，面板与探针就会各报一个数）。
COUNT_FIELDS: tuple[str, ...] = (
    "nodes",
    "non_renderer_objects",
    "renderers",
    "mesh_objects",
    "placeholder_objects",
    "materials",
    "textures",
    "lod_levels",
    "lod0_members",
    "lod_union",
    "lod_outside",
    "lod_hidden",
)


def _fill_counts(report: ImportReport, graph: unit_graph.UnitGraph) -> None:
    """把图上算出来的计数写进报告（**唯一**来源：`unit_graph.counts_of`）。"""
    counts = graph.counts()
    for name in COUNT_FIELDS:
        setattr(report, name, int(counts[name]))


def _crosscheck_counts(report: ImportReport, built: Mapping[str, int]) -> None:
    """建完之后把「真建出来几个」与图上的数核对 —— 对不上进 `report.errors`（⛔ 不静默）。

    为什么还要对：图说的是「包里应该有几个」，建场景那一段说的是「场景里真有几个」。
    两者不同 = 建场景那一段漏了东西（或者图的推法错了），两种都必须当场看见。
    """
    pairs = (
        ("non_renderer_objects", "non_renderer"),
        ("renderers", "renderers"),
        ("mesh_objects", "mesh"),
        ("placeholder_objects", "placeholder"),
        ("lod_hidden", "lod_hidden"),
    )
    for name, key in pairs:
        want = int(getattr(report, name, 0))
        # 字段名 = 图上那个计数（`ImportReport` 与 `counts_of` 逐字同名）
        got = int(built.get(key, 0))
        if want != got:
            report.errors = (
                *report.errors,
                f"计数对不上：{name} 图上 {want} / 真建 {got}",
            )

def _import_from_source(
    graph: unit_graph.UnitGraph,
    report: ImportReport,
    *,
    only_lod0: bool,
    with_vertex_groups: bool,
    with_armature: bool,
    collection_name: str | None,
    vt_maps: bool = True,
    game_root: str | Path | None = None,
) -> None:
    r"""按一个已经打开的来源把一个件建进场景（**唯一**那份导入内核）。

    ★ 2026-10-15：从 import_unit 里**原样搬出来**的一步（搬的那一刻只搬不改；
    同日晚加的是**第 0 步**可瞄武器朝向校正，就是下面 `align_aimable` 那一段）。
    目的只有一个 —— 「catalog 路」与「磁盘上一个 .bundle 文件路」共用**同一份**建场景代码
    （本仓铁律：逻辑只有一份）。⛔ 这里不解析地址、不开包、不找路：那三件在调用方做完。

    :param graph: **已经物化好的图**（`unit_graph.UnitGraph`）—— 树 / 目标集 / 朝向校正 /
        计数全在它身上。⛔ 本函数不解析地址、不开包、不算计数：
        那三件在 `session` / `unit_graph` 里。
        为什么改成这样（2026-10-18 整改）：原来这里自己 `read_prefab_nodes` + `build_tree`，
        而 `materials` / `turret_entry` 各自另开一次包各推一遍子集 ⇒
        推法一分叉，用户看到的就是「材质改了炮塔没变」。现在三方看的是**同一个 graph 对象**。
    :param report: 现场读数 —— **就地填**（同一个 report 对象一路填到底）。
    :param vt_maps: ★ 2026-10-15 默认 **True** = 建完材质后**自动**把 VT 抽出来的
        Normal/Mask 接上（用户：「为什么不像加载材质底图一样，拿出来就加载上？」）。
        门控与读数全在 `blender_addon.materials`（`vt_shader_table` / `vt_decision_lines`）；
        ⛔ 这里不重复一遍判断，也不 import 任何 UI 模块。
    """
    source = graph.source
    tree = graph.tree
    if tree is None:
        raise RuntimeError("图里没有 prefab 树 —— 先用 unit_graph.build_graph 物化一次")
    unit_key = graph.identity.unit_key
    # ---- 0. 可瞄武器朝向校正（★ 2026-10-15）--------------------------------
    # 校正已经发生在 `unit_graph.build_graph` 的**第 0 步**（建树之前）⇒ 网格对象 / 父链 /
    # 骨 / 世界坐标一次性全对（它们都从这**同一棵**树折出来）。⛔ 校正 ≠ 原样，读数照抄。
    if graph.aim is not None:
        report.aim_summary = graph.aim.summary()
        report.aim_mounts = tuple(m.name for m in graph.aim.mounts)
        report.aim_rearward = tuple(m.name for m in graph.aim.rotated)
        report.aim_lines = graph.aim.lines()
        report.aim_issues = graph.aim.issues
    else:
        report.aim_summary = "朝向校正：关（align_aimable=False）⇒ **原样**（资产的静止姿势）"
    lod0 = set(tree.lod_members(0))
    #: **不在任何 LOD 级里**的 renderer（实测 AH-1Z 有 6 个）：它们不是 LOD 变体，
    #: 一律可见。⚠ 本单第一版把「不在 LOD0 里」都当 LOD1+ 隐藏了 —— T-72B_Base 的
    #: 13 个 renderer 恰好全在 LODGroup 里（union == renderers），所以那条规则在
    #: W3 的判据上不显形；换成 US_AH-1Z Viper 就只剩一个飞行员被渲出来。
    always_visible = {r.component_id for r in tree.renderers_outside_lods()}
    #: 这次要建的那批 renderer —— **由图上选**（`graph.targets` 已经是 only_lod0 选过的），
    #: 这里只把 pathID 映回树上的 `Renderer` 对象（建场景那一半要的是树的对象）。
    target_ids = {int(item.path_id) for item in graph.targets}
    targets = [r for r in tree.renderers if int(r.component_id) in target_ids]
    # ★ 2026-10-18 整改：**计数从图导出**（M1 的判据是「ImportReport 从图导出」）。
    #   下面建场景时另有一份**真实**计数（建出来几个对象 / 几个占位），末尾与图核对 ——
    #   对不上就进 report.errors（⛔ 不许静默取一个）。
    _fill_counts(report, graph)
    report.node_names = tuple(n.name for n in tree.nodes)
    report.node_order = tuple(n.transform_id for n in tree.nodes)
    report.node_paths = tuple(n.path for n in tree.nodes)
    report.bones_matched, report.bones_unmatched, report.bone_empty_slots = _bone_check(
        source, tree, targets
    )

    # ---- 1. 材质 & 贴图（只解用到的：units 包 2725 张贴图，全解要几分钟） ----
    # W4：一个材质 -> 一个 MaterialPlan -> 四个角色各取一张图 -> 照 NODE_SHAPE 建节点树。
    material_of: dict[int, str] = {}
    #: 材质名 -> (VT group, VT item)。★ 按**每颗材质**记，不按单位。
    stacks_of: dict[str, tuple[str, str]] = {}
    image_of: dict[int, str] = {}
    decoded: dict[int, tuple[str, bytes, int, int]] = {}
    used_entries: list[dict[str, object]] = []
    for renderer in targets:
        for material_id in renderer.material_ids:
            if material_id in material_of:
                continue
            entry = dict(tree.materials.get(material_id) or {})
            # ★ 2026-10-15（viewport-look 队友真机量到的真凶）：DLC 车（UnitV4_Camo 那批）的
            #   **颜色层只写在 shader 自写的 desc 里**，不读它 ⇒ 拿进来的车体是**一块灰面**。
            #   实测（RU_BMD_Kornet）：读了 desc 之后「接上的材质 1/4 → 3/4 · 有图的节点 3 → 5」，
            #   同机位渲染像素差 mean=0.0872 max=0.8784 changed=20.8%。
            #   ⛔ 读不到就如实不接（plan_material(prop_descs=None)）—— 不许编一个默认值。
            descs = _material_prop_descs(source, int(material_id))
            plan = plan_material(entry, prop_descs=descs or None)
            used_entries.append(entry)
            images, normal_source = _material_images(
                source, plan, unit_key, decoded, image_of, report
            )
            material = blender_materials.build_material_from_plan(
                f"{unit_key}__{entry.get('name') or material_id}",
                _drop_failed_normal(plan, images),
                images,
                normal_source=normal_source,
            )
            if material.users == 0:
                material.use_fake_user = True
            for row in entry.get("build_texture_stacks") or ():
                stacks_of[material.name] = (str(row[0]), str(row[1]))
                break
            material_of[material_id] = material.name
    # 材质 / 贴图的**权威数**在图里；这里只核对「真建出来几个 / 真解出来几张」
    report.textures_decoded = len(image_of)
    if len(material_of) != report.materials:
        report.errors = (
            *report.errors,
            f"材质数：图上 {report.materials} / 真建 {len(material_of)}",
        )
    if len(image_of) != report.textures:
        report.unresolved = (
            *report.unresolved,
            f"贴图：图上引用 {report.textures} 张 / 真解出来 {len(image_of)} 张"
            "（没解出来的见 errors）",
        )
    report.material_names = tuple(sorted(material_of.values()))
    unit_plan = plan_unit(unit_key, used_entries)
    report.material_rows = tuple(_material_row(plan) for plan in unit_plan.materials)
    report.material_unreduced = tuple(item.line() for item in unit_plan.runtime_items)
    report.material_runtime_count = len(report.material_unreduced)
    # ---- 1b. ★ 2026-10-15：「① 拿」就自动把 VT 的 Normal/Mask 接上 -------------
    # 用户原话：「**你不觉得这样很麻烦吗？为什么不像加载材质底图一样，拿出来就加载上？**」
    # 口径（一条都不省）：① 有图就接；② 没图**如实点名**（缺哪张、哪个目录）；
    # ③ 按 **shader** 门控（只碰 VT 栈那批，见 materials.vt_shader_table）；
    # ④ **已有自己法线的那颗一个字节都不动**（履带 / 高机 —— 它们是从包里接好的）。
    # ⛔ 逐材质读数一律走 materials.vt_decision_lines（那里没有第二份接线实现）。
    if vt_maps:
        live = [bpy.data.materials.get(name) for name in report.material_names]
        live = [mat for mat in live if mat is not None]
        _bind_vt_maps(live, stacks_of, report, game_root=game_root)

    # ---- 2. 层级 + 网格（52 个节点一个不少，名字照抄） ----
    collection = bpy.data.collections.new(collection_name or f"BA Mod · {unit_key}")
    bpy.context.scene.collection.children.link(collection)
    report.collection_name = collection.name
    lod_collection = bpy.data.collections.new("LOD1+（默认不参与渲染）")
    # ⛔ 2026-10-17 撤（用户报「我的小模型呢」）：这里曾加 `lod_collection.hide_viewport`，
    #    结果把**唯一尺寸正确的那一份**（LOD1）在视口里藏了，而尺寸错的那份（body2）还留着
    #    ⇒ 用户眼前只剩一台 686 m 的巨物（相机在它体内，什么都看不到）。
    #    **在「哪个 LOD 级尺寸才算对」定下来之前，不许在这里藏视口。**
    collection.children.link(lod_collection)

    renderers_of_node: dict[int, list[prefab_mod.Renderer]] = {}
    for renderer in tree.renderers:
        renderers_of_node.setdefault(renderer.node_id, []).append(renderer)
    report.world_of = {
        node.transform_id: tree.world_position(node.transform_id) for node in tree.nodes
    }

    mesh_cache: dict[int, bpy.types.Mesh] = {}
    #: 建场景时**真数出来**的那几个数（末尾与图核对：⛔ 对不上就进 report.errors）。
    built: dict[str, int] = {
        "non_renderer": 0,
        "renderers": 0,
        "mesh": 0,
        "placeholder": 0,
        "lod_hidden": 0,
    }
    #: ★ 补骨架：**真拿到了顶点组**的网格对象（空槽占位组不算 —— 见 rig_plan.missing_groups）。
    skinned: list[bpy.types.Object] = []
    for node in tree.nodes:
        wanted = [r for r in renderers_of_node.get(node.transform_id, []) if r in targets]
        if not wanted:
            built["non_renderer"] += 1
            obj = bpy.data.objects.new(node.name, None)
            obj.empty_display_size = 0.2
            collection.objects.link(obj)
            report.object_of[node.transform_id] = obj.name
            report.transform_of[obj.name] = int(node.transform_id)  # ★ 变换段要
            _apply_local_transform(obj, node)
            continue
        for renderer in wanted:
            obj = _make_renderer_object(
                source,
                tree,
                node,
                renderer,
                material_of,
                unit_key,
                with_vertex_groups=with_vertex_groups,
                mesh_cache=mesh_cache,
                report=report,
            )
            if obj.name != node.name:
                obj.name = node.name
            if renderer.component_id in lod0 or renderer.component_id in always_visible:
                collection.objects.link(obj)
            else:
                lod_collection.objects.link(obj)
                obj.hide_render = True
                built["lod_hidden"] += 1
            report.object_of[node.transform_id] = obj.name
            report.transform_of[obj.name] = int(node.transform_id)  # ★ 变换段要
            report.mesh_of[obj.name] = {
                "mesh_id": int(renderer.mesh_id or 0),
                "mesh_file": int(renderer.mesh_file or 0),
                "renderer": str(renderer.name),
                "component_id": int(renderer.component_id),
            }
            built["renderers"] += 1
            if obj.type == "MESH":
                built["mesh"] += 1
                if obj.vertex_groups:
                    skinned.append(obj)  # ★ 补骨架的落点（有顶点组才挂得动）
            else:
                built["placeholder"] += 1

    # 父链第二遍（所有对象都建好之后再挂，免得顺序影响结果）
    for node in tree.nodes:
        obj = bpy.data.objects.get(report.object_of.get(node.transform_id, ""))
        if obj is None or node.parent_id is None:
            continue
        parent = bpy.data.objects.get(report.object_of.get(node.parent_id, ""))
        if parent is None:
            report.errors = (*report.errors, f"父节点缺失：{node.path}")
            continue
        obj.parent = parent

    # ---- 2b. ★ 2026-10-17：LOD 档尺寸离群归一（不碰网格、不碰 prefab）----
    cid_of_object = {str(rec.get("component_id")): name for name, rec in report.mesh_of.items()}
    object_of_cid = {
        int(cid): name for cid, name in cid_of_object.items() if str(cid).lstrip("-").isdigit()
    }
    if _LOD_SIZE_NORMALISE and tree.lods:
        report.lod_size_fixes = tuple(_normalise_lod_sizes(tree.lods, object_of_cid))

    # ---- 3. 补骨架（设计稿 §3「蒙皮」：Armature ＋ 顶点组 ＋ 绑定姿势）----
    _build_rig(
        tree,
        targets,
        skinned,
        collection,
        unit_key,
        report,
        with_armature=with_armature,
        with_vertex_groups=with_vertex_groups,
    )
    _crosscheck_counts(report, built)


def import_unit(
    game_root: str | Path,
    unit_key: str,
    *,
    only_lod0: bool = False,
    with_vertex_groups: bool = True,
    with_armature: bool = True,
    collection_name: str | None = None,
    align_aimable: bool = True,
    vt_maps: bool = True,
) -> ImportReport:
    """导一个单位（默认导全部 13 个 renderer，只有 LOD0 参与渲染）。

    ★ 2026-10-12 补上**骨架**（设计稿 §3「蒙皮」那一格）：38 个骨槽 -> 30 根骨
    （29 个真槽 ＋ 把链连起来的「root」）＋ 12 个蒙皮件各挂一个 Armature 修改器。
    没有它，用户按 G 拖 root / body **车纹丝不动**（用户 2026-10-12 亲手验到）。

    ☆ 2026-10-15 补上**可瞄武器朝向校正**（实测 RU_T72B 那挺 Kord 的静止姿势朝车尾）：
    默认 **开**；⛔ 那是**校正、不是 prefab 原样**，读数在 `report.aim_*` 里逐件点名。

    :param only_lod0: True = 只建 LOD0 的 7 个 renderer（材质 2 / 贴图 3）。
    :param with_armature: True = 按骨槽建骨架并挂修改器（要有顶点组才挂得动）。
    :param align_aimable: True = 把可瞄武器的静止朝向校到车头；False **原样**。
    :param vt_maps: ★ 默认 **True** = 建完材质就把 VT 抽出来的 Normal/Mask **自动**接上
        （用户要的就是默认自动）；要它在包里自己那张法线（履带 / 高机）**原样不动**是
        **另一条**门控（`materials.vt_shader_table` ＋「自带法线就跳过」），与本参数无关。
    :raises FileNotFoundError: 找不到 catalog / 没有唯一命中的包。
    :raises KeyError: 地址键在 catalog 里不存在。
    """
    started = time.time()
    report = ImportReport(unit_key=unit_key, only_lod0=only_lod0)
    # ★ 2026-10-18 整改：**开包只有一处**（`session.UnitSession`）—— 地址解析 / 找包 / 开包
    #   全在会话里，这里只拿那一个 graph。⛔ 本函数不再 open_unique_source（判据：同单位三阶段
    #   连跑后 `unitypy_source.OPEN_COUNT` 仍 == 1）。
    #   ⚠ 会话开包时 `deps=True`（实测 250 个 prefab / 824 条材质引用指向**别的包**；不打开依赖
    #   包时 `_read()` 把它们吞成 None ⇒ 履带落到 Blender 默认白）。
    with blender_session.session_for(unit_key, game_root=game_root) as unit_session:
        graph = unit_session.load_graph(
            align_aimable=align_aimable, only_lod0=only_lod0
        )
        report.asset_path = graph.identity.asset_path
        report.candidates = graph.identity.candidates or unit_session.candidates
        report.bundle = graph.identity.bundle
        report.bundle_path = graph.identity.bundle_path
        if unit_session.notes:
            report.unresolved = (*report.unresolved, *unit_session.notes)
        _import_from_source(
            graph,
            report,
            only_lod0=only_lod0,
            with_vertex_groups=with_vertex_groups,
            with_armature=with_armature,
            collection_name=collection_name,
            vt_maps=vt_maps,
            game_root=game_root,
        )
    bpy.context.view_layer.update()
    report.seconds = time.time() - started
    return report


# --------------------------------------------------------------------------- #
# ★ 2026-10-15 · 直接从磁盘上一个 .bundle 拿件（**不走 catalog**）
# --------------------------------------------------------------------------- #
# 由来（用户 2026-10-15 原话）：「**如果我拿的是我新 bundle 包**」—— 「① 拿」原来只认游戏的
# catalog（地址键 -> 候选包 -> 容器里唯一命中的那个）⇒ 自己造的包必须**先顶上去**才出现在列表里。
# 这一节补的是**直接给一个 .bundle 文件**那条路。
#
# 与 catalog 路的关系：**共用**同一份建场景代码（`_import_from_source`）⇒ 产物逐字段同形
# （对象 / 集合 / `mesh_of` / `transform_of` / `world_of` / 骨架，② 改 那格照旧能用）。
#
# | 函数 | 形状 | 干什么 | 开包 |
# |---|---|---|---|
# | `check_bundle_file(p)` | `-> list[str]` | 只读核对：在不在 / 签名 / 大小 / 几件 | 1 次 |
# | `list_bundle(p)` | `-> BundleListing` | 清单数据面（件表 ＋ 类型计数） | 1 次 |
# | `list_bundle_lines(p)` | `-> list[str]` | 上面那份的读数行（面板画它） | 1 次 |
# | `plan_bundle_take(p, asset_path=)` | `-> list[str]` | 干跑：会拿哪一件 / 会建几个对象 | 1 次 |
# | `import_bundle(p, asset=)` | `-> ImportReport` | 真拿进 Blender（同 ① 拿 的形状） | 1 次 |
# | `apply_bundle_take(...)` | `-> ImportReport` | 面板入口名，转调 `import_bundle` | 同上 |
# | `bundle_summary_line(lst)` | `-> str` | 一句人话（<= 60 字） | 不用 |
#
# 认不出来的文件**干净地拒**：一律抛 `BundleFileError`，消息里**点名那个路径**
# （三种实测：不存在 / 空文件 / 前 8 字节不是 UnityFS 家族的签名）。
#
# 件名（`unit_key`）：catalog 路给的是**地址键**（例 `Decal G 1`）；文件路没有地址键 ⇒
# 取那条地址的**文件名主干**（`Assets/Decals/Ground/Decal G 1.prefab` -> `Decal G 1`）——
# 两条路因此**逐字同名**（实测：集合名 / 材质名 / 对象名集合全相同）。要别的名字就传 `key=`。
#
# ⚠ 开销口径：`list_bundle` / `import_bundle` 都是**开一次包**。单位包那种 3.2 GB 的要几十秒
# 与十几 GB 内存 —— 重活（units / vfx / prefabsme）**必须**套 `_work/take/heavy_lock.py`。

#: UnityFS 家族的签名（游戏自己的包全是第一种；后三种是 Unity 的老容器）。
BUNDLE_SIGNATURES = (b"UnityFS", b"UnityWeb", b"UnityRaw", b"UnityArchive")
#: 清单里最多走几条件（⛔ 不是「只拿前 N 件」：超了会在读数里点名说截断了）。
PIECE_LIMIT = 200
#: 摘要行上限（口径沿用：第 1 行 <= 60 字）。
BUNDLE_HEAD_MAX = 60


class BundleFileError(ValueError):
    """这个文件当不成包：不存在 / 是目录 / 空文件 / 签名不对 / 开包失败。

    ⛔ 每一条消息里都**点名那个路径** —— 用户可能同时拖着好几个包，不点名的报错没法查。
    """


@dataclass(slots=True)
class BundlePiece:
    """清单里的一件 = 容器里一条 `.prefab` 地址（面板拿 `to_dict()` 塞进会话）。"""

    asset_path: str
    path_id: int = 0
    nodes: int = 0
    renderers: int = 0
    meshes: int = 0
    materials: int = 0
    textures: int = 0
    bones: int = 0
    problems: tuple[str, ...] = ()

    def line(self) -> str:
        flags = f" · ⚠ {len(self.problems)}" if self.problems else ""
        return (
            f"{self.asset_path} · 节点 {self.nodes} / renderer {self.renderers}"
            f" / 网格 {self.meshes} / 材质 {self.materials} / 贴图 {self.textures}"
            f" / 骨槽 {self.bones}{flags}"
        )

    def to_dict(self) -> dict[str, object]:
        """JSON 友好的形状（**面板要的**；值一律 list，不做 tuple / set）。"""
        return {
            "asset_path": self.asset_path,
            "path_id": int(self.path_id),
            "nodes": int(self.nodes),
            "renderers": int(self.renderers),
            "meshes": int(self.meshes),
            "materials": int(self.materials),
            "textures": int(self.textures),
            "bones": int(self.bones),
            "problems": list(self.problems),
        }


@dataclass(slots=True)
class BundleListing:
    """一个 `.bundle` 文件里**能拿什么**（只读读数）。"""

    path: str
    byte_size: int
    signature: str
    kinds: dict[str, int] = field(default_factory=dict)
    pieces: tuple[BundlePiece, ...] = ()
    container_count: int = 0
    truncated: bool = False
    problems: tuple[str, ...] = ()
    seconds: float = 0.0

    def counts(self) -> dict[str, int]:
        """六类可拿的件 —— 面板按这张表画（「件」= 能拿进 Blender 的那一件）。"""
        return {
            "件": len(self.pieces),
            "网格": int(self.kinds.get("Mesh", 0)),
            "材质": int(self.kinds.get("Material", 0)),
            "贴图": int(self.kinds.get("Texture2D", 0)),
            "动画 clip": int(self.kinds.get("AnimationClip", 0)),
            "蒙皮件(骨架)": int(self.kinds.get("SkinnedMeshRenderer", 0)),
        }

    def summary(self) -> str:
        """一行长的读数（全量；面板抬头那份走 `bundle_summary_line`）。"""
        counts = self.counts()
        return (
            f"{Path(self.path).name} · {self.signature} · {self.byte_size:,} B"
            f" · 件 {counts['件']} / 网格 {counts['网格']} / 材质 {counts['材质']}"
            f" / 贴图 {counts['贴图']} / 动画 {counts['动画 clip']}"
        )

    def lines(self) -> list[str]:
        """读数行：第 1 行 = 摘要（<= 60 字），其余进「看详情」。"""
        out = [bundle_summary_line(self), self.summary()]
        out.append(f"路径：{self.path}")
        out.append(f"容器条目 {self.container_count} 条 · 可拿的件 {len(self.pieces)} 件")
        out.append("类型计数：" + "、".join(f"{k}={v}" for k, v in sorted(self.kinds.items())))
        if self.truncated:
            out.append(f"⚠ 件只列了前 {PIECE_LIMIT} 条（清单被截断 —— 不是只有这么多）")
        for piece in self.pieces:
            out.append("  " + piece.line())
            for problem in piece.problems:
                out.append("    ⚠ " + problem)
        for problem in self.problems:
            out.append("  ⚠ " + problem)
        if self.seconds:
            out.append(f"开包计时：{self.seconds:.1f} s")
        return out


def bundle_signature(path: str | Path) -> bytes:
    """只看前 8 个字节认签名（⛔ 不开包）—— 认不出来的文件在这一步就干净地拒。"""
    target = Path(path)
    if not target.exists():
        raise BundleFileError(f"没有这个文件：{target}")
    if target.is_dir():
        raise BundleFileError(f"这是一个目录，不是 .bundle 文件：{target}")
    if target.stat().st_size == 0:
        raise BundleFileError(f"空文件（0 字节）：{target}")
    with target.open("rb") as handle:
        head = handle.read(8)
    for signature in BUNDLE_SIGNATURES:
        if head.startswith(signature):
            return signature
    raise BundleFileError(
        f"不是 UnityFS 包（前 8 字节 {head!r}）：{target}"
        " —— 认得的签名是 UnityFS / UnityWeb / UnityRaw / UnityArchive"
    )


def open_bundle_file(path: str | Path) -> tuple[unitypy_source.AssetSource, str]:
    """签名过了再开包。返回 `(读源, 签名文本)`；**调用方负责 close**。

    ⛔ UnityPy 那边的任何异常都**换壳**成 `BundleFileError`（点名路径）—— 别把
    「字典遍历时变了大小」这类实现细节漏到面板上。
    """
    target = Path(path)
    signature = bundle_signature(target)
    try:
        return unitypy_source.open_source(target), signature.decode("ascii", "replace")
    except Exception as exc:  # 打不开就是打不开，换壳点名（重抛 ⇒ 不需要 BLE001）
        raise BundleFileError(f"打不开这个包：{target}（{type(exc).__name__}: {exc}）") from exc


def _key_of_asset_path(asset_path: str) -> str:
    """地址 -> 件名：取**文件名主干**（`Assets/Decals/Ground/Decal G 1.prefab` -> `Decal G 1`）。

    与 catalog 路的地址键**逐字同名**（实测 `Decal G 1`），所以两条路的集合名 / 材质名 /
    对象名集合全相同 —— 这就是「同一个包走两条路 → 对象名集合相同」那条判据。
    """
    return Path(str(asset_path).replace("\\", "/")).stem


def _pick_bundle_asset(
    container: tuple[tuple[str, int], ...],
    asset_path: str | None,
    target: Path,
    kinds: Mapping[str, int],
) -> str:
    """挑那一件：给了就用给的；没给就自己挑（`.prefab` 且**唯一**那条）。

    ⛔ 0 条 / 多条都**点名拒** —— 与 catalog 路「候选包命中多个不许猜」同一个口径。
    """
    paths = [path for path, _pid in container]
    if asset_path:
        if asset_path not in paths:
            raise BundleFileError(
                f"这个包（{target}）里没有地址 {asset_path!r}"
                f"（容器条目 {len(paths)} 条；例 {paths[:3]}）"
            )
        return asset_path
    prefabs = [path for path in paths if path.lower().endswith(".prefab")]
    if len(prefabs) == 1:
        return prefabs[0]
    if not prefabs:
        raise BundleFileError(
            f"这个包里没有可拿的件（一条 .prefab 地址都没有）：{target}"
            f"（容器条目 {len(paths)} 条；类型计数 {dict(sorted(kinds.items()))}）"
            " —— 贴图 / Sprite 一类不是「件」；要拿件请给一个含 prefab 的包"
        )
    raise BundleFileError(
        f"这个包（{target}）里有 {len(prefabs)} 条 .prefab 地址，**不知道你要哪一件**"
        f"（前三条 {prefabs[:3]}）—— 请显式给 asset_path"
    )


def _piece_of(source: unitypy_source.AssetSource, asset_path: str, path_id: int) -> BundlePiece:
    """走一遍这一件（节点 / renderer / 网格 / 材质 / 贴图 / 骨槽）；坏件只记账，⛔ 不废清单。"""
    nodes, materials = source.read_prefab_nodes(asset_path)
    tree = prefab_mod.build_tree(nodes, materials)
    mesh_ids: set[int] = set()
    problems: list[str] = []
    for renderer in tree.renderers:
        if not renderer.mesh_id:
            problems.append(f"{renderer.name}：没有网格（renderer 的 m_Mesh 是空的）")
            continue
        if renderer.mesh_file:
            problems.append(
                f"{renderer.name}：网格指向外部文件 m_FileID={renderer.mesh_file}"
                f"（pathID={renderer.mesh_id}），本包没有这个网格"
            )
            continue
        mesh_ids.add(int(renderer.mesh_id))
    bones = 0
    for renderer in tree.renderers:
        bones = max(bones, sum(1 for bone in renderer.bone_ids if bone))
    return BundlePiece(
        asset_path=asset_path,
        path_id=int(path_id),
        nodes=len(tree.nodes),
        renderers=len(tree.renderers),
        meshes=len(mesh_ids),
        materials=len(tree.materials),
        textures=len(tree.texture_ids()),
        bones=bones,
        problems=tuple(problems),
    )


def list_bundle(path: str | Path, *, with_pieces: bool = True) -> BundleListing:
    """列一个 `.bundle` 里**能拿什么**：件（prefab）＋ 网格 / 骨架 / 材质 / 贴图 / 动画 clip。

    `with_pieces=False` 只给类型计数（不逐件走树，快）。开销 = **开一次包**。

    :raises BundleFileError: 不存在 / 是目录 / 空 / 签名不对 / 打不开（消息里点名路径）。
    """
    started = time.time()
    target = Path(path)
    source, signature = open_bundle_file(target)
    try:
        kinds = {str(k): int(v) for k, v in source.kind_counts().items()}
        container = source.container_entries()
        pieces: list[BundlePiece] = []
        problems: list[str] = []
        truncated = False
        if with_pieces:
            wanted = [row for row in container if row[0].lower().endswith(".prefab")]
            if len(wanted) > PIECE_LIMIT:
                truncated = True
                wanted = wanted[:PIECE_LIMIT]
            for asset_path, path_id in wanted:
                try:
                    pieces.append(_piece_of(source, asset_path, path_id))
                except Exception as exc:  # noqa: BLE001 -- 单件坏了不废整张清单
                    problems.append(f"{asset_path}: {type(exc).__name__}: {exc}")
        return BundleListing(
            path=str(target),
            byte_size=int(target.stat().st_size),
            signature=signature,
            kinds=kinds,
            pieces=tuple(pieces),
            container_count=len(container),
            truncated=truncated,
            problems=tuple(problems),
            seconds=time.time() - started,
        )
    finally:
        source.close()


def list_bundle_lines(path: str | Path, *, with_pieces: bool = True) -> list[str]:
    """`list_bundle` 的读数行（第 1 行 = 摘要）。认不出来的文件直接抛 `BundleFileError`。"""
    return list_bundle(path, with_pieces=with_pieces).lines()


def check_bundle_file(path: str | Path) -> list[str]:
    """**只读核对**：这个文件能不能当包用、里面有几件（零写入）。

    与 `list_bundle_lines` 是同一份读数（check 就是「列一遍」）—— 认不出来就抛。
    """
    return list_bundle_lines(path)


def bundle_summary_line(listing: BundleListing) -> str:
    """一句人话（<= `BUNDLE_HEAD_MAX` 字；面板抬头画它）。"""
    text = listing.summary()
    if len(text) <= BUNDLE_HEAD_MAX:
        return text
    return text[: BUNDLE_HEAD_MAX - 1] + "…"


def plan_bundle_take(
    path: str | Path, asset_path: str | None = None, *, key: str | None = None
) -> list[str]:
    """**干跑**：从磁盘上这个 `.bundle` 拿件**会拿到什么**（零写入 · 不进 Blender 场景）。

    与真拿的区别只在最后一步：这里**不建对象**；开包 / 挑件 / 走树 / 数数与真拿一模一样。
    """
    listing = list_bundle(path)
    chosen = _pick_piece(listing, asset_path)
    unit_key = key or _key_of_asset_path(chosen.asset_path)
    out = [
        f"干跑（一个字节都不写）：{Path(str(path)).name} -> 拿 {chosen.asset_path}",
        f"件名 {unit_key!r}（集合 / 材质 / 对象名都用它）",
        f"建出来：节点 {chosen.nodes} · renderer {chosen.renderers}"
        f"（网格 {chosen.meshes} + 占位 {chosen.renderers - chosen.meshes}）"
        f" · 材质 {chosen.materials} · 贴图 {chosen.textures} · 骨槽 {chosen.bones}",
        f"清单：{bundle_summary_line(listing)}",
    ]
    out.extend("  ⚠ " + problem for problem in chosen.problems)
    out.append(f"对象数（真拿之后应与之相同）：{chosen.nodes}")
    return out


def _pick_piece(listing: BundleListing, asset_path: str | None) -> BundlePiece:
    """从清单里挑一件（干跑用）。⛔ 0 条 / 多条 / 给错了都点名拒。"""
    if asset_path:
        for piece in listing.pieces:
            if piece.asset_path == asset_path:
                return piece
        raise BundleFileError(
            f"这个包（{listing.path}）里没有地址 {asset_path!r}"
            f"（可拿的件 {len(listing.pieces)} 件；例 "
            f"{[p.asset_path for p in listing.pieces[:3]]}）"
        )
    if len(listing.pieces) == 1:
        return listing.pieces[0]
    if not listing.pieces:
        raise BundleFileError(
            f"这个包里没有可拿的件（一条 .prefab 地址都没有）：{listing.path}"
            f"（容器条目 {listing.container_count} 条；类型计数 {listing.counts()}）"
            " —— 贴图 / Sprite 一类不是「件」；要拿件请给一个含 prefab 的包"
        )
    raise BundleFileError(
        f"这个包（{listing.path}）里有 {len(listing.pieces)} 件，**不知道你要哪一件**"
        f"（前三条 {[p.asset_path for p in listing.pieces[:3]]}）—— 请显式给 asset_path"
    )


def import_bundle(
    path: str | Path,
    asset_path: str | None = None,
    *,
    key: str | None = None,
    only_lod0: bool = False,
    with_vertex_groups: bool = True,
    with_armature: bool = True,
    collection_name: str | None = None,
    align_aimable: bool = True,
    vt_maps: bool = True,
) -> ImportReport:
    """从磁盘上一个 `.bundle` **文件**把一个件拿进 Blender（**不走 catalog**）。

    产物与 `import_unit` **同形**：同一个 `ImportReport`，`mesh_of` / `transform_of` /
    `world_of` / `collection_name` 一样填；差别只有两处 ——

    * `bundle` = 文件名，`bundle_path` = **磁盘上这个文件的路径**（② 改 写回要按它开包）；
    * `candidates` = 这个文件名（文件路没有候选包）。

    :param asset_path: 要哪一件（容器里的地址）。不给我就挑**唯一**那条 `.prefab`。
    :param key: 件名（对象 / 材质 / 集合的名字前缀）。不给就取地址的文件名主干。
    :param align_aimable: 同 `import_unit` —— 可瞄武器朝向校正，默认开（**校正 ≠ 原样**）。
    :param vt_maps: 同 `import_unit` —— 建完材质自动接 VT 的 Normal/Mask（默认开）。
        ⚠ 这一路**没有单位键**时（地址主干认不出 / 用户传了别的 `key=`）会找不到那一对图，
        那时**如实点名**、一颗都不接（⛔ 不猜一对图接上去）。
    """
    started = time.time()
    target = Path(path)
    report = ImportReport(unit_key=str(key or ""), only_lod0=only_lod0)
    # 文件路也走**同一个会话**（开包只有一处）；不给 asset_path 就由会话挑唯一那条 .prefab。
    with blender_session.session_for(
        bundle_path=target, asset_path=str(asset_path or ""), unit_key=str(key or "")
    ) as unit_session:
        graph = unit_session.load_graph(align_aimable=align_aimable, only_lod0=only_lod0)
        chosen = graph.identity.asset_path
        unit_key = str(key or _key_of_asset_path(chosen))
        graph.identity = replace(graph.identity, unit_key=unit_key)
        report.unit_key = unit_key
        report.asset_path = chosen
        report.bundle = target.name
        report.bundle_path = str(target)
        report.candidates = (target.name,)
        _import_from_source(
            graph,
            report,
            only_lod0=only_lod0,
            with_vertex_groups=with_vertex_groups,
            with_armature=with_armature,
            collection_name=collection_name,
            vt_maps=vt_maps,
        )
    bpy.context.view_layer.update()
    report.seconds = time.time() - started
    return report


def apply_bundle_take(
    path: str | Path, asset_path: str | None = None, **options: object
) -> ImportReport:
    """**真拿**（面板入口名）—— 转调 `import_bundle`，只有一份实现。"""
    return import_bundle(path, asset_path, **options)  # type: ignore[arg-type]


def _build_rig(
    tree: prefab_mod.PrefabTree,
    targets: list[prefab_mod.Renderer],
    skinned: list[bpy.types.Object],
    collection: bpy.types.Collection,
    unit_key: str,
    report: ImportReport,
    *,
    with_armature: bool,
    with_vertex_groups: bool,
) -> None:
    """★ 补骨架：建骨 ＋ 给蒙皮件挂 Armature 修改器（设计稿 §3「蒙皮」那一格）。

    四步，每步都落读数（⛔ 不静默）：

    1. `rig_plan.plan_rig` 折出骨（纯数据，不碰 bpy）；
    2. `armature.build_armature` 建现场（骨名**回读核对**，fail-closed）；
    3. `attach_modifiers` 逐件挂修改器；
    4. `rest_max_delta` 量「补骨架有没有把模型挪位」—— **必须 ~0**。

    ⚠ **不改父链**：网格对象仍挂在 prefab 的节点底下（"出"那条通道按对象父链还原节点树），
    Armature 修改器不需要父子关系照样生效。

    ⛔ 骨架坏了**不废掉整次导入**（与贴图 / 网格一个口径），但**必须点名**：
    进 `report.rig_error`，它会出现在 `summary()` 里（用户当场看得见）。
    """
    if not with_armature:
        return
    plan = rig_plan.plan_rig(tree, targets, unit_key=unit_key)
    report.bones = len(plan.bones)
    report.bones_deform = len(plan.deform_names)
    report.bone_names = plan.names
    report.rig_issues = plan.issues
    if not with_vertex_groups:
        report.rig_error = "没勾「建顶点组」—— 骨建了也没有权重可挂，本次不建骨架"
        return
    if not plan.bones:
        report.rig_error = "；".join(plan.issues) or "没有可建的骨"
        return
    try:
        armature_object = blender_armature.build_armature(
            plan, name=f"{unit_key}_Armature", collection=collection
        )
    except Exception as exc:  # noqa: BLE001 -- 骨架坏了不废整次导入，但必须点名
        report.rig_error = f"{type(exc).__name__}: {exc}"
        return
    report.armature = armature_object.name
    report.armature_modified = blender_armature.attach_modifiers(armature_object, skinned)
    bpy.context.view_layer.update()
    report.armature_rest_delta = blender_armature.rest_max_delta(skinned)

    issues = list(plan.issues)
    if report.armature_rest_delta > 1e-4:
        issues.append(
            f"rest 姿势下顶点最大位移 {report.armature_rest_delta:.3e} m —— 补骨架把模型挪位了"
            "（该值必须 ~0；尺子 = armature.rest_max_delta）"
        )
    stray: set[str] = set()
    for obj in skinned:
        stray.update(rig_plan.missing_groups(plan, (g.name for g in obj.vertex_groups)))
    if stray:
        issues.append(
            f"{len(stray)} 个顶点组的名字不在骨里（它们**静默不生效**）：{sorted(stray)[:6]}"
        )
    report.rig_issues = tuple(issues)
    report.rig_summary = replace(plan, issues=report.rig_issues).summary()


# --------------------------------------------------------------------------- #
# Blender 侧的小工具
# --------------------------------------------------------------------------- #
def _apply_local_transform(obj: bpy.types.Object, node: prefab_mod.Node) -> None:
    """把 Unity 的局部 TRS 经 coords **唯一那份**换算写到对象上。"""
    obj.location = coords.unity_to_blender(node.position)
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = coords.unity_to_blender_quat(node.rotation)
    obj.scale = coords.unity_to_blender_scale(node.scale)


def _make_image(
    name: str, width: int, height: int, rgba_topdown: bytes, color_space: int
) -> bpy.types.Image:
    """RGBA（PIL 行序：**上到下**）-> Blender Image（pixels 从**左下**开始）。

    ⛔ **颜色空间必须在写像素之前设**（本单实测的坑）：byte 图像的
    colorspace_settings.name 一改，Blender 会把像素缓冲**清成 (0,0,0,255)** ——
    写完之后再设，图就成了纯黑不透明（而尺寸 / 名字 / colorspace 全对、不报错）。
    实测 64² / 512² / 2048² 三种尺寸都一样；先设后写就正常。
    """
    image = bpy.data.images.new(name, width=width, height=height, alpha=True)
    apply_colorspace(image, color_space)  # 见上面的坑：这一句必须在写像素之前
    stride = width * 4
    flat = bytearray()
    for row in range(height - 1, -1, -1):
        flat += rgba_topdown[row * stride : (row + 1) * stride]
    try:
        import numpy as np

        buffer = np.frombuffer(bytes(flat), dtype=np.uint8).astype(np.float32) / 255.0
        image.pixels.foreach_set(buffer)
    except ImportError:
        image.pixels.foreach_set([b / 255.0 for b in flat])
    image.update()
    return image


def _decode_texture(
    source: unitypy_source.AssetSource,
    texture_id: int,
    unit_key: str,
    decoded: dict[int, tuple[str, bytes, int, int]],
    report: ImportReport,
) -> tuple[str, bytes, int, int] | None:
    """贴图 pathID -> (Blender 图名, **原始** RGBA, 宽, 高)；同一张只解一次、只建一次图。

    ⛔ 颜色空间口径一个字不许改：仍走 colorspace_of + _make_image（后者"先设
    colorspace、后写像素"，见它的 docstring）。
    """
    cached = decoded.get(texture_id)
    if cached is not None:
        return cached
    try:
        info = source.read_texture(texture_id)
        rgba = source.decode_texture_rgba(texture_id)
    except Exception as exc:  # noqa: BLE001 -- 单张贴图坏了不该废掉整次导入
        report.errors = (*report.errors, f"贴图 {texture_id}: {type(exc).__name__}: {exc}")
        return None
    image = _make_image(f"{unit_key}__{info.name}", info.width, info.height, rgba, info.color_space)
    if info.hash_mismatch:
        report.unresolved = (
            *report.unresolved,
            f"贴图 {info.name}：m_StreamData.path 的哈希与节点名不一致（走了唯一后缀降级）",
        )
    if image.colorspace_settings.name != colorspace_of(info.color_space):
        report.errors = (
            *report.errors,
            f"贴图 {info.name}：colorspace 设成了 {image.colorspace_settings.name}",
        )
    report.image_colorspace[image.name] = image.colorspace_settings.name
    entry = (image.name, rgba, info.width, info.height)
    decoded[texture_id] = entry
    return entry


def _make_renderer_object(
    source: unitypy_source.AssetSource,
    tree: prefab_mod.PrefabTree,
    node: prefab_mod.Node,
    renderer: prefab_mod.Renderer,
    material_of: dict[int, str],
    unit_key: str,
    *,
    with_vertex_groups: bool,
    mesh_cache: dict[int, bpy.types.Mesh],
    report: ImportReport,
) -> bpy.types.Object:
    """一个 renderer -> 一个对象（网格缺失时建空物体占位，**不静默丢**）。"""
    data: MeshData | None = None
    mesh: bpy.types.Mesh | None = None
    if renderer.mesh_id and not renderer.mesh_file:
        # 缓存键带上**材质槽数**：同一个 mesh 被「1 槽」与「2 槽」的 renderer 共用时，
        # material_index 不一样 ⇒ 不能共用同一份 bpy Mesh（2026-10-17 的 submesh 修复）。
        cache_key = (renderer.mesh_id, len(renderer.material_ids))
        cached = mesh_cache.get(cache_key)
        if cached is not None:
            mesh = cached
        else:
            try:
                data = source.read_mesh(renderer.mesh_id)
                mesh = _mesh_from_data(
                    f"{unit_key}__{data.name}", data, material_slots=len(renderer.material_ids)
                )
                mesh_cache[cache_key] = mesh
            except Exception as exc:  # noqa: BLE001 -- 读不到就退回占位，并记账
                report.errors = (
                    *report.errors,
                    f"网格 {renderer.mesh_id}: {type(exc).__name__}: {exc}",
                )
                mesh = None
    elif renderer.mesh_id:
        report.unresolved = (
            *report.unresolved,
            f"{node.name}: 网格 PPtr 指向外部文件 m_FileID={renderer.mesh_file}"
            f"（pathID={renderer.mesh_id}），本包没有这个网格",
        )
    obj = bpy.data.objects.new(node.name, mesh)
    _apply_local_transform(obj, node)
    for material_id in renderer.material_ids:
        name = material_of.get(material_id)
        if name and obj.data is not None:
            obj.data.materials.append(bpy.data.materials[name])
    if mesh is not None and data is not None and with_vertex_groups and renderer.bone_ids:
        _make_vertex_groups(obj, tree, renderer, data)
    return obj


#: UV 图层名（**唯一一份**）：主 UV 与 **迷彩层（第二套 UV）**。
#: 实测 2026-10-16（`DLC2_XA180EST`）：载具 **9/9** 个网格有 `uv1(dim2)`、驾驶员 **0/3** ——
#: 作者的迷彩就是走第二套 UV（`Shader Graphs/UnitV4_Camo` 的 `Camo` 层）。
UV_LAYER_MAIN = "UVMap"
UV_LAYER_CAMO = "UVMap_Camo"


def uv_layer_plan(data: MeshData) -> tuple[tuple[str, tuple[tuple[float, float], ...]], ...]:
    """要建哪几个 UV 图层（图层名 -> **每顶点** UV：`(layer_name, rows)`）。

    **纯函数**（一点 bpy 都不碰）—— `_mesh_from_data` 照它建层，用例直接断言它。
    ⛔ **没这条通道就不建空层**：空层会让导出 / 烘焙看着像有 UV，其实整片 (0,0)。
    """
    plan: list[tuple[str, tuple[tuple[float, float], ...]]] = []
    if data.uv0:
        plan.append((UV_LAYER_MAIN, data.uv0))
    if data.uv1:
        plan.append((UV_LAYER_CAMO, data.uv1))
    return tuple(plan)


def _mesh_from_data(name: str, data: MeshData, *, material_slots: int = 0) -> bpy.types.Mesh:
    """MeshData -> bpy Mesh（位置 / 法线 / **两套 UV** 照抄，不让 Blender 重算）。

    UV 图层按 `uv_layer_plan` 建：主 UV = `UVMap`，**迷彩层 = `UVMap_Camo`**（源里有 uv1 才有）。

    ★ 2026-10-17：按 `data.submeshes` 写 `polygon.material_index`。
    不写的话**多材质 renderer 的副材质一个面都没有**（实测：玻璃槽 `polys=0`，
    于是「玻璃 alpha 改 1.0」与「保持 0.051」两张渲染逐像素差 0）。
    """
    positions = [coords.unity_to_blender(p) for p in data.positions]
    normals = [coords.unity_to_blender(n) for n in data.normals]
    indices = data.indices
    faces = [tuple(indices[i : i + 3]) for i in range(0, len(indices) - 2, 3)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(positions, [], faces)
    mesh.update()
    for layer_name, rows in uv_layer_plan(data):
        layer = mesh.uv_layers.new(name=layer_name)
        for loop in mesh.loops:
            layer.data[loop.index].uv = rows[loop.vertex_index]
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    # submesh -> 材质槽号（`m_SubMeshes` 的顺序就是 renderer `m_Materials` 的顺序）
    if data.submeshes and material_slots > 1:
        for slot_index, (start, count) in enumerate(data.submeshes[:material_slots]):
            if count <= 0:
                continue
            stop = min(start + count, len(mesh.polygons))
            for poly_index in range(start, stop):
                mesh.polygons[poly_index].material_index = slot_index
    # 设计稿 §8 硬规则 2 / 工单 §7 坑 3：法线照抄，不让 Blender 重算。
    mesh.normals_split_custom_set_from_vertices(normals)
    mesh.update()
    return mesh


def _vertex_group_names(tree: prefab_mod.PrefabTree, renderer: prefab_mod.Renderer) -> list[str]:
    """骨骼槽 -> 顶点组名（**按槽号**，空槽也要占一位，否则权重整体错位）。

    ⛔ **本函数不是实现，只是转调** —— 名字是合同（Armature 修改器按名字找骨），
    而 `rig_plan.slot_names_of` 才是全仓唯一那份（它自己的注释第 199 行就写着这一条，
    2026-10-16 之前**没兑现**：这里曾逐字抄了第二份，迟早漂移）。
    """
    return list(rig_plan.slot_names_of(tree, renderer))


def _make_vertex_groups(
    obj: bpy.types.Object,
    tree: prefab_mod.PrefabTree,
    renderer: prefab_mod.Renderer,
    data: MeshData,
) -> None:
    """按 SMR 的骨骼槽建顶点组并灌权重（数据照抄 mesh 的 blend 通道）。"""
    names = _vertex_group_names(tree, renderer)
    groups = [obj.vertex_groups.new(name=name) for name in names]
    limit = len(groups)
    for vertex_index, (weights, slots) in enumerate(
        zip(data.bone_weights, data.bone_indices, strict=False)
    ):
        for weight, slot in zip(weights, slots, strict=False):
            if weight <= 0.0 or slot < 0 or slot >= limit:
                continue
            groups[slot].add([vertex_index], float(weight), "REPLACE")


def _bone_check(
    source: unitypy_source.AssetSource,
    tree: prefab_mod.PrefabTree,
    targets: list[prefab_mod.Renderer],
) -> tuple[int, int, int]:
    """骨骼名哈希的端到端核对（设计稿 §8 规则 4 / 工单 §13 R12）。"""
    for renderer in targets:
        if renderer.kind != prefab_mod.SKINNED_MESH_RENDERER or not renderer.mesh_id:
            continue
        if renderer.mesh_file:
            continue
        try:
            hashes = skeleton.read_bone_hashes(source.read_typetree(renderer.mesh_id))
            if not hashes:
                continue
            check = skeleton.verify_bone_hashes(tree.bone_paths(renderer.bone_ids), hashes)
        except Exception:  # noqa: BLE001 -- 这是附带读数，不该让导入失败
            continue
        return len(check.matched), len(check.unmatched), check.empty_slots
    return 0, 0, 0
