"""W4 · 材质节点树执行器：照 MaterialPlan 建 Blender 节点树，并读回参数。

本模块是**执行层**（插件侧）：形状在 asset_kernel/materials.py 的 NODE_SHAPE 里定死，
这里只"照计划施工"。

★ 2026-10-15 起它还有**第二件事**：`restore_part` —— **按件的材质还原**，把"包里那颗件的材质"
变成 `bpy.data.images` ＋ 接好线的 Principled（见下面那一节）。⚠ 边界没变：**Unity 二进制的解析
仍然只有一份**（asset_kernel 的 unitypy_source / texture / colorspace），本模块只**编排**顺序 ——
⛔ 不自己解 chunk、不自己拼 typetree、不自己写 z = sqrt(1-x²-y²)。

## 三条硬口径

1. **法线（DXT5nm）**：z 只许由 asset_kernel.normals 那一份公式算 —— 本模块调
   normals.dxt5nm_to_rgb_normal 烘一张**派生图**接进节点树，⛔ 不在节点里再写一遍
   z = sqrt(1 - x² - y²)。原始 DXT5nm 图**照 W3 的样子保留**（W3 的 D4 判据逐像素比的就是它）。
2. **MaskMap**（HDRP 约定，本作实测一致）：R → Metallic；A → Invert → Roughness；
   G → AO **标注忽略**（Principled 没有 AO 输入，只在报告/面板里显形，不接线）。
3. **颜色空间**：派生法线图必须 Non-Color，且**先设 colorspace、后写像素**
   （W3 实测：byte 图像一改 colorspace_settings.name 就把像素清成 (0,0,0,255)）。

## 按件的材质还原（`restore_part`）—— 本模块的第二件事

| 函数 | 干什么 |
|---|---|
| `route_plan_lines(entries)` | 干跑：每颗材质哪几路会接、哪几路没接（不读图） |
| `restore_part(source, asset_path, key=)` | 真做：读贴图 → 建/复用图片 → 照 NODE_SHAPE 接 |
| `restore_bundle(unit_session, asset_path, key=)` | 同上，先让会话开包（⛔ 本模块不自己开） |
| `part_summary(report)` | 一句人话：哪几路真接上了、哪几路没接 |

三路（＋自发光）的落点与**通道对应**（口径 = kernel.NODE_SHAPE，⛔ 不另写一份）：

* **基色** ← `Layer_<hash>` / `_BaseColorMap` / `_MainTex` → `Base Color`；
* **法线** ← `_NormalMap` / `_VirtualDetailNormal` → `Normal`（经 Normal Map 节点）；
* **MaskMap** ← `_MaskMap` / `Layer_<hash>`(desc=Mask) → `Metallic` ＋ `Roughness`；
* **自发光** ← `_EmissiveColorMap` / `_Emission` → `Emission Color`（有图才接）。

通道：基色 / 自发光 = 整张颜色；法线 = **派生图**（DXT5nm 的 A→x、G→y，z 走
`normals.dxt5nm_to_rgb_normal`）；MaskMap = **R→Metallic；A→Invert→Roughness；
G(AO) 只标注，⛔ 不接线**（Principled 没有 AO 输入）。

## 幂等（同一颗件跑两次结果一样）

* 图：同名同尺寸 ⇒ **直接复用**（不重写像素）；尺寸变了才换一张（⛔ 不静默拿错那张）；
* 材质：**就地重建**（`rebuild_material` 先 `nodes.clear()`）—— ⛔ 不新建同名材质：
  场景里那件是按**材质对象**挂槽的，换了对象等于"改了反而空了"；
* 判据 = `RestoreReport.fingerprint()`（节点树 ＋ 接线 ＋ 每路的槽/图/尺寸/颜色空间）。

## ⛔ 这是近似（面板与读数里必须一起写出来）

* 游戏 shader（`UnitV3` / `UnitV4_Camo` …）是**随包携带的编译件**，Blender 没有对应物；
* **Granite VT** 在 Blender 没有对应物 —— 实测 UnitV3 的 `Layer_<hash>`(desc=Normal/Mask)
  在**材质的 m_TexEnvs 里根本没有**（不是我们没接，是静态资产里就没有）；
* ⇒ 只还原"哪张贴图上哪一路"，**不是游戏观感**；遮蔽 / 倒影 / 条纹只有用户能判。

## 本机实测（Blender 5.2.1 LTS / Python 3.13，--factory-startup）

- **material.blend_method 在 5.2 已废弃**（RNA 描述逐字："Blend Mode for Transparent
  Faces (Deprecated: use 'surface_render_method')"）。实测逐个赋值再读回：
  OPAQUE→**HASHED** / CLIP→**HASHED** / HASHED→HASHED / BLEND→BLEND
  （写 BLEND 会连带把 surface_render_method 设成 BLENDED，其余是 DITHERED）
  ⇒ OPAQUE / CLIP 在 5.2 **没有独立档位**，照抄旧版本的 blend_method='CLIP' 是**静默无效**的。
  真正让 alpha cutoff 生效的是 material.alpha_threshold（RNA："A pixel is rendered only if
  its alpha value is above this threshold"）—— CLIP 那一档由 _apply_alpha_mode 一并写它。
- ShaderNodeSeparateColor 在 5.2 **只有 Red / Green / Blue 三个输出、没有 Alpha**
  ⇒ MaskMap 的 A 通道直接从 ShaderNodeTexImage 的 Alpha 输出取。
- ShaderNodeInvert：Factor(VALUE) + Color(RGBA) → Color(RGBA)，默认 Factor=1 ⇒ 输出 1−Color。
- Principled 数值输入真名（4.0 起改过：Emission→Emission Color、Clearcoat→Coat Weight）
  **按当前实例逐个核**（principled_input_name），核不到抛 KeyError，不静默接错。
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

import bpy

from ba_mod_maker.asset_kernel import materials as kernel
from ba_mod_maker.asset_kernel import normals
from ba_mod_maker.asset_kernel import textures as kernel_textures
from ba_mod_maker.asset_kernel.colorspace import apply_colorspace, colorspace_of

from . import session

try:  # Blender 自带 numpy；缺失时退回纯 Python（与 importer._make_image 同一套兜底）
    import numpy as _np
except ImportError:  # pragma: no cover -- Blender 里不会走到
    _np = None

__all__ = [
    "NORMAL_RGB_SUFFIX",
    "PACKED_SOURCE_PREFIX",
    "PART_APPROX_NOTE",
    "PLACEHOLDER_PREFIX",
    "SOURCE_DISK",
    "SOURCE_PACKED",
    "SOURCE_PLACEHOLDER",
    "USER_MAP_APPROX_NOTE",
    "BindReadout",
    "BindReport",
    "MaterialReadout",
    "RestoreReport",
    "RouteReadout",
    "VT_MAP_DIR_ENV",
    "VT_MAP_SUFFIX",
    "VT_ORIGIN",
    "VtReadout",
    "bake_normal_image",
    "bind_user_map",
    "bind_user_maps",
    "build_material_from_plan",
    "vt_decision_lines",
    "vt_decisions",
    "vt_shader_table",
    "game_map_dir",
    "game_map_files",
    "game_name_of",
    "has_own_normal",
    "map_source_label",
    "material_by_game_name",
    "material_candidate_rows",
    "material_shader",
    "material_to_nodes",
    "packed_image_by_name",
    "packed_map_image",
    "param_probe",
    "param_rows_for",
    "part_summary",
    "principled_input_name",
    "read_material_params",
    "rebuild_material",
    "restore_bundle",
    "restore_part",
    "route_plan_lines",
    "shader_prop_descs",
    "user_map_sources",
]

#: 派生法线图的后缀（Lead 冻结的形状）。
NORMAL_RGB_SUFFIX = "__NormalRGB"

# --------------------------------------------------------------------------- #
# ① 拿 时自动接 VT 法线 / 遮罩 —— **路径约定 ＋ 门控**（⛔ 全在这一个文件里）
# --------------------------------------------------------------------------- #
# 由来（用户 2026-10-15 原话）：「**你不觉得这样很麻烦吗？为什么不像加载材质底图
# 一样，拿出来就加载上？**」—— 底图在 ① 拿 时自动接好了，而 VT 抽出来的 Normal/Mask
# 要用户在 ② 改 里**逐材质点**。这一节就是让 ① 拿 一次到位的那一条：**图在哪** ＋
# **哪几颗该接**；真正的接线仍在 importer.py 调 bind_user_maps（⛔ 只有一份实现）。
#
# ⛔ 为什么是 materials.py 而不是面板：importer.py 不许 import panels_*（UI 层）。
# 面板那一份（panels_edit.game_map_dir / game_map_files）由 Lead 收尾去重 ——
# ⛔ 本文件不碰 panels_edit.py。

#: 「用游戏原图」去哪个目录找抽出来的图（BAMOD_VT_DIR 可覆盖 —— 抽图那条线的落点）。
#: ★ 2026-10-18：字符串**只有一份**（`session.GAME_MAP_DIR_ENV`）—— 本模块只转发它的名字，
#: ⛔ 不再自己写一遍字面量（同一个字符串写两处，改一处忘一处是最老的坑）。
VT_MAP_DIR_ENV = session.GAME_MAP_DIR_ENV
#: 两张图各自的**文件名约定**（⛔ 不写死 item ID：那串 hash 会随游戏更新消失，文件名不会）。
VT_MAP_SUFFIX: Mapping[str, str] = {
    kernel.ROLE_NORMAL: "_normal_decoded.png",
    kernel.ROLE_MASK: "_mask_raw.png",
}
#: 读数抬头（与 ② 改 那一格「用游戏原图」同口径）。
VT_ORIGIN = "游戏原图（VT 抽出来的）"

#: VT 层的槽名族：UnitV3 的 _VirtualDetailNormal / Layer_<hash>(desc=Normal|Mask)。
#: ⛔ **不按 shader 名字写死** —— 按 kernel 映射表里**确实声明了**的槽判（见 vt_shader_table）。
VT_SLOT_PREFIXES: tuple[str, ...] = ("_VirtualDetailNormal", "Layer_")


#: ★ 2026-10-18：**实现只有一份**（`session.game_map_dir`）—— 这里只留一个转发名，
#: ⛔ 不再有 `def game_map_dir`（交接文档 §4 判据 1：`rg -c "^def game_map_dir"` 必须是 1）。
#: 语义不变：`BAMOD_VT_DIR` 优先，否则仓内 `_work/vt-extract`。
game_map_dir = session.game_map_dir


def game_map_files(unit_key: str = "") -> tuple:
    """(paths, note) —— 「用游戏原图」要的两张图。

    挑法（按**文件名约定 ＋ 单位键**，逐级放宽；⛔ 绝不按 item ID 挑）：

    1. <单位键>_normal_decoded.png / <单位键>_mask_raw.png（首选）；
    2. **只有 `unit_key` 为空时**才退到「目录里唯一一份」那个兜底；
       ⛔ 2026-10-17 改：**unit_key 非空而对不上 ⇒ 一张都不接**（如实点名）。
       旧版无条件兜底 ⇒ 抽图目录里只有别的单位的图时，会**静默接错单位**（实测：只放了
       RU_T72B 那两张时，导入 ACV / AH-1W 都接上了 T-72B 的法线与遮罩）；
    3. 多份候选又对不上单位键 ⇒ note 里明写「分不清」，由调用方报红（⛔ 不替你猜一个）；
    4. ★ task-8：磁盘上这一路**没有** ⇒ 再看**场景里有没有同名打包图**（`packed_map_image`）——
       用户把图打包进 .blend 之后，那一对文件常常已经不在抽图目录里了。命中时
       `paths[role]` 给的是**图名**（不是路径），由 `_load_user_image` 认出来，读数会说
       「从 .blend 里打包的图」。

    ⚠ 为什么**磁盘优先**：这一格的契约是「抽出来的游戏原图」那个目录（`BAMOD_VT_DIR`）；
    打包图是那一对文件不在时的兜底。（`_load_user_image` 里反过来是「打包优先」—— 那是
    「已经拿到一个名字了，去用哪张」的问题，不是「该挑哪一张」的问题。）
    """
    root = game_map_dir()
    paths: dict = {}
    notes: list = []
    for role, suffix in VT_MAP_SUFFIX.items():
        candidates = sorted(root.glob("*" + suffix)) if root.is_dir() else []
        if not candidates:
            packed, packed_note = packed_map_image(role, unit_key)
            if packed is not None:
                paths[role] = str(packed.name)
                notes.append(packed_note or f"{role}：用**打包在 .blend 里的图** {packed.name}")
                continue
            if root.is_dir():
                notes.append(f"缺 {role}：{root} 里没有 *{suffix}")
            if packed_note:
                notes.append(packed_note)
            continue
        keyed = [item for item in candidates if unit_key and item.name.startswith(unit_key)]
        if keyed:
            paths[role] = str(keyed[0])
            continue
        if len(candidates) == 1 and not unit_key:
            paths[role] = str(candidates[0])
            notes.append(f"{role}：按唯一候选取的 {candidates[0].name}")
            continue
        if len(candidates) == 1:
            # ⛔ 2026-10-17：单位键对不上 ⇒ **不接**。旧版这里无条件用「唯一候选」，
            #    实测后果：`_work/vt-extract/` 里只有 RU_T72B 那一份时，**导入 ACV / AH-1W
            #    都会被接上 T-72B 的法线与遮罩**（不报错，只在 note 里写「按唯一候选取的」）。
            #    与 importer.py「读不到就如实不接，不许编一个默认值」同口径。
            notes.append(
                f"对不上单位 {role}：{root} 里只有 {candidates[0].name}，与 {unit_key} 无关 ⇒ 不接"
            )
            continue
        names = "、".join(item.name for item in candidates[:4])
        notes.append(f"分不清 {role}：{root} 里有 {len(candidates)} 份候选（{names}）")
    if not paths and not root.is_dir():
        return {}, f"这里还没有抽出来的游戏原图：{root}（{VT_MAP_DIR_ENV} 可指到别处）"
    return paths, ("；".join(notes) if notes else "")


def vt_shader_table(shader: str):
    """这个 shader 的映射表 —— 有 **VT 栈**（Normal/Mask 在 VT 层）才返回它。

    ⛔ 不猜：认不出的 shader（例 HDRP/Unlit）返回 None；表里 Normal/Mask 明确是
    **自己的 2D 槽**的（HDRP/Lit 的 _NormalMap / Infantry 的 _Normal）也返回 None ——
    那几颗的 Normal/Mask 是包里的静态图，接 VT 图只会把人家原本接好的东西顶掉。

    判据（两条都取自 kernel 的表，⛔ 这里不写死任何 shader 名字）：

    1. `normal_slots` / `mask_slots` 里有 VT 槽名族的槽（UnitV3 的 _VirtualDetailNormal）——
       「Normal 确实声明成了 VT 层」；或
    2. **整张表是 desc 驱动的**（`base_color_descs` 非空）—— M2 `Shader Graphs/UnitV3` 与
       M4 `Shader Graphs/UnitV4_Camo` 这一族的槽名里带 hash（Layer_<8 位>），静态 2D 槽
       一个都取不到：UnitV3 的真材质实测 `Layer_4C4561B0`(Mask) 在 m_TexEnvs 里命中 0，
       M4 的 57 颗真材质里 `_VirtualDetailNormal` / `_Emission` **一个都没绑**（kernel
       M2/M4 的 note 逐字写着「Normal/Mask 是 VT 层，静态取不到」）。

    ⚠ 为什么要第 2 条：只按第 1 条会**漏掉 UnitV4_Camo**（DLC2 那批车）—— M4 表按设计
    不声明 normal/mask 槽（静态取不到），那条路会是「明明有 VT 图却一颗都不接」。
    ⚠ M3 `Shader Graphs/Infantry Shader` 有确切槽名（_Normal/_Mask）⇒ 两条都不成立 ⇒
    **不动它**（如实：步兵那批没量到 VT 栈）。
    """
    table = kernel.table_for(shader)
    if table is None:
        return None
    slots = (*table.normal_slots, *table.mask_slots)
    if any(slot.startswith(prefix) for slot in slots for prefix in VT_SLOT_PREFIXES):
        return table
    if table.base_color_descs:
        return table
    return None


@dataclass(frozen=True, slots=True)
class VtReadout:
    """一颗材质在「① 拿 自动接 VT 图」这一跳上的读数（⛔ 每种结局都要能显形）。

    skipped 的三种取值就是三条判据：no_normal（自带法线，负控）/ no_image（没图，
    如实说）/ not_vt（不是 VT shader，不动）。
    """

    material: str
    shader: str = ""
    bound: bool = False
    skipped: str = ""
    #: 接上的每一路：`(角色, 图名, 来源)` —— 来源是 `SOURCE_DISK` / `SOURCE_PACKED`
    #: （★ task-8：读数必须点名「图是从磁盘读的，还是从 .blend 里打包的」）。
    used: tuple[tuple[str, str, str], ...] = ()
    detail: str = ""

    def line(self) -> str:
        """一行读数 —— 第 1 行摘要（面板/探针都读它）。"""
        if self.bound:
            used = "；".join(
                f"{role}={Path(name).name}" + (f"（{label}）" if label else "")
                for role, name, label in self.used
            )
            return f"{self.material}｜接上：{used}"
        head = {
            "no_normal": "自带法线 ⇒ 不动",
            "no_image": "没图 ⇒ 不接",
            "not_vt": "不是 VT shader ⇒ 不动",
        }.get(self.skipped, f"{self.skipped or '没动'} ⇒ 不动")
        tail = f"（{self.detail}）" if self.detail else ""
        return f"{self.material}｜{head}{tail}"


def has_own_normal(material: bpy.types.Material) -> str:
    """这颗材质**已经有自己的法线**了吗 —— 有就返回那张图名，没有返回空串。

    判据 = **按实际连线反查**（_images_by_role，⛔ 不查我们记在旁边的账）。正因为看的是
    **连线**，所以用户自己画的图 / 包里的派生图都算「自带」，重跑两遍也稳。法线接没接
    **不靠 _PROP_NORMAL_SOURCE 那个属性** —— 面板接的图（bamod_user_normal_png）没有它，
    拿它当依据会把「已经接好的」判成「没接」。
    """
    try:
        wired = _images_by_role(material.node_tree, _principled_of(material))
    except Exception:  # noqa: BLE001 -- 读不出连线 ⇒ 当作「没有」，由调用方自己的读数兜底
        return ""
    return str(wired.get(kernel.ROLE_NORMAL) or "")


def role_maps(paths: Mapping[str, str]) -> dict:
    """`vt_cache` 的 `{"normal","mask"}` -> VT 角色键（⛔ 角色名只在这一处映射）。"""
    return {
        kernel.ROLE_NORMAL: str(paths.get("normal") or ""),
        kernel.ROLE_MASK: str(paths.get("mask") or ""),
    }


def vt_decisions(
    materials: Iterable[bpy.types.Material],
    maps: Mapping[str, str],
    *,
    missing: str = "",
) -> list:
    """逐材质**做决定**（该接的就接）：接上 / 自带法线跳过 / 没图如实说 / 不是 VT 不动。

    这是「① 拿 自动接 VT 图」的**唯一**一份判断（⛔ `importer.py` 不再写一遍）。

    :param materials: 这次导入建出来的材质（import_unit / import_bundle 的产物）。
    :param maps: 角色 -> 磁盘上那张图；空 dict = 这一对图**不存在**（那时只出读数、不接线）。
    :param missing: 没图时那句人话（点名缺的是哪张 / 哪个目录）。
    :returns: 每颗材质一条 `VtReadout`（读数是**结构**，渲染成字符串是调用方的事 ——
        这样「接上了几颗」不用去字符串里捞）。
    """
    rows: list = []
    for material in materials:
        shader = material_shader(material)
        if vt_shader_table(shader) is None:
            rows.append(VtReadout(material.name, shader, skipped="not_vt"))
            continue
        # ⚠ 顺序有讲究：**自带法线**先判 —— 没图时「这颗有自己的法线」比「没图」更该显形
        #   （两种情况下都**一个字节都不动**，差别只在读数对不对得上）。
        own = has_own_normal(material)
        if own:
            rows.append(
                VtReadout(material.name, shader, skipped="no_normal", detail=f"已有法线 {own}")
            )
            continue
        if not maps:
            rows.append(VtReadout(material.name, shader, skipped="no_image", detail=missing))
            continue
        # ⚠ 这一对图**不全**时两路都不接：`bind_user_maps` 的契约是「先取齐两张再动节点树」，
        #   少一张它会当场 ValueError —— 那会把整次「① 拿」带崩（只接一半更不行）。
        absent = [role for role in (kernel.ROLE_NORMAL, kernel.ROLE_MASK) if not maps.get(role)]
        if absent:
            heads = "、".join(_ROUTE_LABEL.get(role, role) for role in absent)
            rows.append(
                VtReadout(
                    material.name,
                    shader,
                    skipped="no_image",
                    detail=f"这一对图不全（缺 {heads}）⇒ 两路都不接（⛔ 不半接）",
                )
            )
            continue
        report = bind_user_maps(
            material,
            normal_png=str(maps.get(kernel.ROLE_NORMAL) or ""),
            mask_png=str(maps.get(kernel.ROLE_MASK) or ""),
            origin=VT_ORIGIN,
        )
        wired = report.wired()
        labels = {route.role: route.source_label for route in report.routes}
        used = tuple(
            (role, str(wired.get(role) or ""), str(labels.get(role) or ""))
            for role in (kernel.ROLE_NORMAL, kernel.ROLE_MASK)
            if wired.get(role)
        )
        rows.append(VtReadout(material.name, shader, bound=True, used=used))
    return rows


def vt_decision_lines(
    materials: Iterable[bpy.types.Material],
    maps: Mapping[str, str],
    *,
    missing: str = "",
) -> list:
    """`vt_decisions(...)` 的读数行（面板与探针画的就是它）。"""
    return [row.line() for row in vt_decisions(materials, maps, missing=missing)]


#: 材质自定义属性：material_to_nodes / read_material_params 靠它把不在节点树里的信息报出来。
_PROP_NORMAL_SOURCE = "bamod_normal_source"
_PROP_ALPHA_MODE = "bamod_alpha_mode"
#: plan.scalars 里**真实存在**的游戏参数名（逗号分隔）。read_material_params 只读回这些 ——
#: 否则会把 Blender 的默认值当成游戏参数读回来（W5 会凭空写进游戏材质）。
_PROP_SCALAR_NAMES = "bamod_scalar_names"
#: 被外观护栏改写过 Principled Alpha 时，把**游戏原值**存这里。
#: 为什么：_AlphaCutoff 的落点是 Principled Alpha（kernel 的参数表），而 OPAQUE/CLIP 必须把
#: Alpha 强制 1.0（见 _force_opaque_alpha）；不存原值的话 read_material_params 会把
#: _AlphaCutoff 读成 1.0，W5 就把 1.0 写回游戏材质 —— 静默改数据。
_PROP_ALPHA_CUTOFF = "bamod_alpha_cutoff"
#: ★ 2026-10-17：Alpha 是从哪来的（`_BaseColor.a`）—— 读数与面板照实说，⛔ 不猜。
_PROP_ALPHA_SOURCE = "bamod_alpha_source"
#: 上面那个属性的参数名（PARAM_TO_NODE_INPUT 的键）。
_PARAM_ALPHA_CUTOFF = "_AlphaCutoff"

#: pathID -> 派生图名（同一次会话里同一张法线图只烘一次，重复调不重复烘）。
_BAKED_NORMAL: dict[int, str] = {}

#: 逻辑输入名 -> 当前 Blender 上的真名候选（第一个命中就用）。
#: 顺序 = 新版名在前、旧版名在后（4.0 是分界线，见设计稿 §4 D4）。
_PRINCIPLED_NAMES: Mapping[str, tuple[str, ...]] = {
    "Base Color": ("Base Color",),
    "Metallic": ("Metallic",),
    "Roughness": ("Roughness",),
    "IOR": ("IOR",),
    "Alpha": ("Alpha",),
    "Normal": ("Normal",),
    "Emission Color": ("Emission Color", "Emission"),
    "Emission Strength": ("Emission Strength",),
    "Coat Weight": ("Coat Weight", "Clearcoat"),
    "Coat Roughness": ("Coat Roughness", "Clearcoat Roughness"),
    "Coat Normal": ("Coat Normal", "Clearcoat Normal"),
    "Specular IOR Level": ("Specular IOR Level", "Specular"),
    "Transmission Weight": ("Transmission Weight", "Transmission"),
    "Thin Film Thickness": ("Thin Film Thickness",),
    "Thin Film IOR": ("Thin Film IOR",),
    "Sheen Weight": ("Sheen Weight", "Sheen"),
    "Subsurface Weight": ("Subsurface Weight", "Subsurface"),
}

#: ChannelLink.channel -> ShaderNodeSeparateColor 的输出名（实测 5.2 没有 Alpha）。
_SEPARATE_OUT: Mapping[str, str] = {"R": "Red", "G": "Green", "B": "Blue"}


# --------------------------------------------------------------------------- #
# Principled 的输入名（按版本查，不猜）
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _principled_input_names() -> frozenset[str]:
    """实测一遍当前 Blender 的 Principled 输入名（建一个探针材质、读完立刻删掉）。"""
    probe = bpy.data.materials.new("__bamod_principled_probe__")
    try:
        probe.use_nodes = True
        node = next((n for n in probe.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if node is None:  # pragma: no cover -- Blender 自带 Principled
            raise RuntimeError("Blender 的 Principled 节点没出现，无法按版本查输入名")
        return frozenset(socket.name for socket in node.inputs)
    finally:
        bpy.data.materials.remove(probe)


def principled_input_name(logical: str) -> str:
    """逻辑输入名 -> 当前 Blender 上 Principled 的**真名**。

    ⛔ 不许照抄网上的旧名（工单 §7 坑 5：Blender 4.0 起 Emission→Emission Color、
    Clearcoat→Coat Weight…）。这里拿**当前实例的输入名**逐个核，核不到抛 KeyError。
    """
    candidates = _PRINCIPLED_NAMES.get(logical, (logical,))
    available = _principled_input_names()
    for name in candidates:
        if name in available:
            return name
    raise KeyError(
        f"Blender {bpy.app.version_string} 的 Principled 没有 {logical!r} 这个输入；"
        f"候选 {candidates} 都不在实测输入名里（共 {len(available)} 个）"
    )


def _principled_logical(logical: str) -> str:
    """PARAM_TO_NODE_INPUT 的逻辑名可能带中文括注（如 "Alpha（配合 blend_method=CLIP）"）。"""
    return logical.split("（", 1)[0].strip()


# --------------------------------------------------------------------------- #
# 贴图 -> Blender Image
# --------------------------------------------------------------------------- #
def _new_image(
    name: str, width: int, height: int, rgba_topdown: bytes, color_space: int
) -> bpy.types.Image:
    """RGBA（PIL 行序：上到下）-> Blender Image（pixels 从左下开始）。

    ⛔ **颜色空间必须在写像素之前设**：W3 实测 byte 图像一改 colorspace_settings.name，
    Blender 会把像素缓冲清成 (0,0,0,255)（尺寸 / 名字 / colorspace 全对、不报错）。
    """
    image = bpy.data.images.new(name, width=width, height=height, alpha=True)
    apply_colorspace(image, color_space)  # 见 docstring：这一句必须在写像素之前
    stride = width * 4
    flat = bytearray()
    for row in range(height - 1, -1, -1):
        flat += rgba_topdown[row * stride : (row + 1) * stride]
    if _np is not None:
        buffer = _np.frombuffer(bytes(flat), dtype=_np.uint8).astype(_np.float32) / 255.0
        image.pixels.foreach_set(buffer)
    else:  # pragma: no cover -- Blender 自带 numpy
        image.pixels.foreach_set([b / 255.0 for b in flat])
    image.update()
    return image


def bake_normal_image(
    texture_id: int, name: str, rgba: bytes, width: int, height: int
) -> bpy.types.Image:
    """DXT5nm 原图 -> 派生 RGB 法线图（名字 = 原图名 + __NormalRGB，colorspace = Non-Color）。

    z 的重建**只走** asset_kernel.normals.dxt5nm_to_rgb_normal（⛔ 这里没有公式副本）。
    按 texture_id 缓存：同一张法线图重复调返回**同一张** Blender Image，不重复烘
    （跨材质/跨单位引用同一张贴图时也复用；**名字取第一次创建的那次**）。

    :raises ValueError: 字节数与 width*height*4 对不上（不静默烘一张错图）。
    """
    expected = width * height * 4
    if len(rgba) != expected:
        raise ValueError(f"法线图字节数 {len(rgba)} != width*height*4 = {expected}")
    cached = _BAKED_NORMAL.get(texture_id)
    if cached is not None:
        existing = bpy.data.images.get(cached)
        if existing is not None:
            return existing
    rgb = normals.dxt5nm_to_rgb_normal(rgba)  # X=A、Y=G -> R=x、G=y、B=(z+1)/2、A=255
    image = _new_image(f"{name}{NORMAL_RGB_SUFFIX}", width, height, rgb, 0)  # 0 = Non-Color
    _BAKED_NORMAL[texture_id] = image.name
    return image


def _image_node(
    nodes: bpy.types.Nodes, image_name: str, location: tuple[float, float]
) -> bpy.types.Node:
    image = bpy.data.images.get(image_name)
    if image is None:
        raise ValueError(f"images 里的图 {image_name!r} 在 bpy.data.images 里不存在")
    node = nodes.new("ShaderNodeTexImage")
    node.image = image
    node.location = location
    return node


def _channel_output(
    tex: bpy.types.Node, separate: bpy.types.Node | None, channel: str | None
) -> bpy.types.NodeSocket | None:
    """ChannelLink.channel -> 输出 socket。

    None = 整张颜色；"A" 走 TexImage 自己的 Alpha（实测 5.2 的 Separate Color
    没有 Alpha 输出）；"R"/"G"/"B" 走 Separate Color；认不出来返回 None（不接）。
    """
    if channel is None:
        return tex.outputs["Color"]
    if channel == "A":
        return tex.outputs["Alpha"]
    socket_name = _SEPARATE_OUT.get(channel)
    if socket_name is None or separate is None:
        return None
    return separate.outputs[socket_name]


def _link_maybe_invert(
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    source: bpy.types.NodeSocket,
    target: bpy.types.NodeSocket,
    invert: bool,
    *,
    tag: str | None = None,
) -> None:
    """source -> target；invert=True 时中间插一个 Invert（Roughness = 1 − Smoothness）。

    :param tag: 给那颗 Invert 打标签 —— ⛔ 必须传：不打标签的重跑会**每跑一次多一颗 Invert**
        （2026-10-15 探针当场抓到的幂等 bug：法线那两路清得掉、Mask 这颗清不掉）。
    """
    if invert:
        node = nodes.new("ShaderNodeInvert")
        node.location = (-450.0, -400.0)
        if tag:
            _tag_node(node, tag)
        links.new(source, node.inputs["Color"])
        links.new(node.outputs["Color"], target)
    else:
        links.new(source, target)


# --------------------------------------------------------------------------- #
# 建材质
# --------------------------------------------------------------------------- #
def _record_scalar_names(material: bpy.types.Material, plan: kernel.MaterialPlan) -> None:
    """把 plan.scalars 里**真实存在**的游戏参数名记到材质上（读回时只有这些能出去）。

    ★ v0.17.0：顺手把**这张映射表的身份**（M1..M4 的 key 与 shader 名）也记下来 ——
    ④ 改 · 外观 的材质候选要拿它当显示名，参数表要拿它判断「这一条驱不驱动渲染」
    （`_NON_RENDERING_SCALARS` 是按 shader 分组的）。⛔ 没有它就只能靠材质名猜，那是猜。
    """
    names = [param for param, _value in plan.scalars if param in kernel.PARAM_TO_NODE_INPUT]
    material[_PROP_SCALAR_NAMES] = ",".join(names)
    # ⚠ 字段在 **MaterialPlan 上**（`table_key` / `shader`），不在 plan.table 上 ——
    #   第一版写成 getattr(plan, "table", None) ⇒ 两个属性恒为空串（真机探针当场抓到的）。
    material[_PROP_TABLE_KEY] = str(getattr(plan, "table_key", "") or "")
    material[_PROP_SHADER] = str(getattr(plan, "shader", "") or "")
    material[_PROP_GAME_NAME] = str(getattr(plan, "material_name", "") or "")
    # 原值（不是名字）也要留：_NON_RENDERING_SCALARS 那类参数会被换成哑光值渲染，
    # 写回必须读到**游戏原值**，否则静默改数据。这里拿得到 material，所以记在这。
    material[_PROP_GAME_SCALARS] = json.dumps(
        {param: float(value) for param, value in plan.scalars}, sort_keys=True
    )


def _saved_scalar(mat: bpy.types.Material, param: str) -> float | None:
    """材质上另存的**游戏原值**（没有就是 None，调用方回退到 socket）。

    见 _NON_RENDERING_SCALARS：shader 不读的参数被换成哑光值渲染，原值另存以免写回失真。
    """
    raw = mat.get(_PROP_GAME_SCALARS)
    if not raw:
        return None
    try:
        saved = dict(json.loads(str(raw)))
    except ValueError:  # pragma: no cover -- 属性被人手改坏时回退到 socket
        return None
    value = saved.get(param)
    return None if value is None else float(value)


def _recorded_scalar_names(mat: bpy.types.Material) -> set[str] | None:
    """材质上记的游戏参数名；**没有这个属性**返回 None（手搓 / 旧材质 ⇒ 退回"全读回"）。"""
    raw = mat.get(_PROP_SCALAR_NAMES)
    if raw is None:
        return None
    return {name.strip() for name in str(raw).split(",") if name.strip()}


#: 实测「shader 不读、因而**不许驱动渲染**」的标量（W5 判决实验，见
#: docs/交接-2026-10-03-W5-B1首个写回.md:54 —— 车体一次改 10 个参数，画面一点没变）。
#: ⚠ 不驱动渲染 ≠ 可以丢掉：写回还要把游戏原值**原样**读回去，否则就是静默改游戏的数。
#: ⚠ 2026-10-07 更正：UnitV3 的真名是 `_Metal`（shader 第 10 行），**不是** `_Metallic`
#: —— `_Metallic` 在这族材质里也存在但一律 0.0（那是 HDRP/Lit 的默认值，换 shader 留下的死数据）。
#: 改名前这里点的是 UnitV3 根本不读的属性 ⇒ 这条 skip 其实一直没生效。
#: 这些标量由插件侧**带条件**接（⛔ 不走 `_apply_scalars` 的 1:1 写入）。
#:   * `_TransmissionEnable` → 只在 alpha_mode=BLEND 时写 Transmission Weight；
#:   * `_IridescenceThickness` / `_IridescenceMask` → 只在 `_MATERIAL_FEATURE_IRIDESCENCE` 在册时写
#:     Thin Film Thickness（先过 `_IridescenceThicknessRemap`，再 ×1000 变纳米）。
_PLUGIN_WIRED_SCALARS: frozenset[str] = frozenset({
    "_TransmissionEnable",
    #: ★ 2026-10-17：涂装层的 UV 平铺/偏移由 `_apply_camo` 自己用（不是 socket）。
    "_OffsetX",
    "_OffsetY",
    "_Layer_step",
    "_IridescenceThickness",
    "_IridescenceMask",
    "_AlphaRemapMin",
    "_AlphaRemapMax",
})

_NON_RENDERING_SCALARS: Mapping[str, frozenset[str]] = {
    "Shader Graphs/UnitV3": frozenset({"_Metal", "_Smoothness"}),
}

#: 存游戏原始标量的材质自定义属性（JSON）。
_PROP_GAME_SCALARS = "bamod_game_scalars"

#: ★ v0.17.0：这张材质走的是哪张映射表（M1..M4）+ 它的 shader 名。
#: 用途：④ 的材质候选显示名、"这一条驱不驱动渲染"的判断。⛔ 不靠材质名猜。
_PROP_TABLE_KEY = "bamod_table_key"
_PROP_SHADER = "bamod_shader"

#: ★ v0.17.0 · **游戏里的材质名**（`MaterialPlan.material_name`）。
#: ⛔ 这一格不能省：Blender 里的材质名是**带单位前缀**的（实测 RU_T72B 导入后叫
#:   `RU_T72B__RU_T72B_track`），而写回是按**游戏里的 m_Name**（`RU_T72B_track`）去包里找目标的。
#:   少了它，④ 的候选会把带前缀的名字交给写回 ⇒ dry run 直接找不到目标
#:   （**真机链路探针当场抓到的**，不是猜的）。
_PROP_GAME_NAME = "bamod_material_name"

#: 用户 2026-10-04 报的症状：「为什么在 Blender 里反光这么强？而在游戏里是哑光的」
#: —— 根因就是 _Smoothness=1.0 被当成了渲染输入，Roughness = 1 − 1 = 0 = 抛光镜面。
#: 不写 socket 之后 Roughness 停在 Blender 的 0.5（半哑光）；若还嫌亮，下一手是给 M2 一个
#: **写明理由**的哑光常量（0.6~0.8），而不是再走"从游戏参数推"这条已被证伪的路。


def _principled_defaults(mat: bpy.types.Material, principled: bpy.types.Node) -> dict[str, float]:
    """Principled 的数值输入 + **游戏原值覆盖**。

    ⚠ 为什么要有覆盖：UnitV3 那种「shader 不读」的标量，socket 上放的是给渲染用的哑光值，
    写回要的是游戏原值 —— 两者必须分开存，否则写回会静默改游戏数据。
    """
    out = {
        socket.name: round(float(socket.default_value), 6)
        for socket in principled.inputs
        if socket.type == "VALUE"
    }
    raw = mat.get(_PROP_GAME_SCALARS)
    if not raw:
        return out
    try:
        saved = dict(json.loads(str(raw)))
    except ValueError:  # pragma: no cover -- 属性被人手改坏时不炸面板
        return out
    for param, value in saved.items():
        mapping = kernel.PARAM_TO_NODE_INPUT.get(param)
        if mapping is None:
            continue
        logical, invert = mapping
        if logical.startswith("Normal Map."):
            continue
        name = principled_input_name(_principled_logical(logical))
        if name in out:
            out[name] = round((1.0 - float(value)) if invert else float(value), 6)
    return out


def _clamp_to_socket(socket: bpy.types.NodeSocket, value: float) -> float:
    r"""把值夹进 socket 的 RNA 硬边界（取不到边界就原样返回）。

    ★ 2026-10-17 实测：`Shader Graphs/UnitV3` 的玻璃类材质 `_Smoothness` **实测 2.13**，
    `_Smoothness -> Roughness` 那条带 invert ⇒ 写进去 **-1.13**。Blender 渲染时静默当 0
    （全镜面），而 socket 上留着一个不可能的值 —— 读数难看、也容易被人当成真读数。
    ⛔ 只夹**渲染用**的 socket 值；游戏原值另存在 `bamod_game_scalars`，写回不受影响。
    """
    try:
        prop = socket.bl_rna.properties["default_value"]
        low, high = float(prop.hard_min), float(prop.hard_max)
    except (KeyError, AttributeError, TypeError, ValueError):  # pragma: no cover
        return value
    if high <= low:  # pragma: no cover
        return value
    return max(low, min(high, value))


def _mask_image_wired(plan: kernel.MaterialPlan, images) -> bool:
    """这颗材质**有没有**接上 MaskMap 贴图（没有 ⇒ 光滑度得靠 VT，本轮读不到）。"""
    slot = plan.slot_of(kernel.ROLE_MASK)
    if slot is None or slot.texture_id is None:
        return False
    if images is None:
        return False
    return int(slot.texture_id) in images


def _apply_scalars(principled: bpy.types.Node, plan: kernel.MaterialPlan, images=None) -> None:
    """把 plan.scalars 写进 Principled 的数值输入（含 _Smoothness 的反向）。

    ⚠ 即使这个输入**随后会被贴图连上**也照样写 default_value —— 这样 read_material_params
    能把游戏参数原样读回来（W5 写回要用），而渲染仍然走贴图。
    """
    skip = _NON_RENDERING_SCALARS.get(str(getattr(plan, "shader", "")), frozenset())
    raw: dict[str, float] = {}
    for param, value in plan.scalars:
        raw[param] = float(value)
        if param in skip:
            continue
        mapping = kernel.PARAM_TO_NODE_INPUT.get(param)
        if mapping is None:
            continue
        logical, invert = mapping
        if logical.startswith("Normal Map."):
            continue  # 由 Normal Map 节点的 Strength 承载（_wire_normal 里写）
        # ⛔ 2026-10-17：`_AlphaCutoff` / `_AlphaClipThreshold` 这类是**阈值**，不是不透明度。
        #   旧版被 PARAM_TO_NODE_INPUT 映射到 Principled 的 **Alpha** ⇒ BLEND 档材质里
        #   写进去的就是 cutoff（实测 `DLC2_UH60_glass` 的 `_AlphaCutoff`=0.0 ⇒ Alpha=0 ⇒
        #   玻璃在 Blender 里**完全看不见**）。真阈值走 `material.alpha_threshold`，
        #   见 `_apply_alpha_mode`；写回要的原值也记在那里。
        if logical.startswith("Alpha"):
            continue
        # ★ 2026-10-17 第二轮：这三个由插件侧**带条件**接（见 `_apply_transmission` /
        #   `_apply_iridescence`），⛔ 不能 1:1 直接写 socket：`_TransmissionEnable` 默认 1.0，
        #   直接写会把 725 颗不透明 HDRP/Lit 全变成玻璃；虹彩厚度要先过 remap 再 ×1000。
        if param in _PLUGIN_WIRED_SCALARS:
            continue
        socket = principled.inputs.get(principled_input_name(_principled_logical(logical)))
        if socket is None:  # pragma: no cover -- principled_input_name 已保证存在
            continue
        # ★ 2026-10-17 用户：「载入皮肤后看的时候有很强的反光」——根因就这一条：
        #   `_Smoothness` 在数据里能超 1（Yak130 机身 1.1 / 玻璃 2.13），`1 - 1.1 = -0.1`
        #   被夹成 **0** ⇒ 全镜面。⛔ 而 HDRP 里逐像素光滑度是 **MaskMap 的 A 通道**给的
        #   （DLC 车族那层是 VT/Granite，本插件还没读）⇒ 没有贴图时把这个饱和值当渲染输入就是造假。
        #   规则：**没有 MaskMap 贴图 且 原值超出 [0,1]** ⇒ 不写这个 socket（Roughness 留在 0.5），
        #   原值照旧记进 `bamod_game_scalars`，写回不受影响；并在材质上留一条读数说明。
        out_of_range = invert and value > 1.0 and not _mask_image_wired(plan, images)
        if out_of_range:
            principled.id_data["bamod_smoothness_note"] = (
                f"{param}={value:g} 超出 0..1 且没有 MaskMap 贴图 ⇒ 不写 Roughness（留 0.5）"
            )
            continue
        mapped = (1.0 - value) if invert else float(value)
        socket.default_value = _clamp_to_socket(socket, mapped)
    # skip 的那些参数**一个 socket 都不写** ⇒ Roughness/Metallic 停在 Blender 自建的默认
    # （Roughness 实测 0.5 = 半哑光）。原值由 _record_scalar_names 另存，写回照旧精确。
    # ⛔ 不要再往 socket 上塞"哑光值"：_record_scalar_names 在本函数**之后**才跑，
    #    塞了也拿不到原值兜底，就成了"渲染好看、写回读错"——静默改游戏数据。


def _wired_normal_texture_id(plan: kernel.MaterialPlan, images: Mapping[int, str]) -> int | None:
    """plan 里 normal 角色要用的 pathID；没接就 None。

    :raises ValueError: 有 normal 角色、但调用方没给派生图（**不许**拿原 DXT5nm 图顶替）。
    """
    if not any(link.role == kernel.ROLE_NORMAL and link.wired for link in plan.links):
        return None
    slot = plan.slot_of(kernel.ROLE_NORMAL)
    texture_id = slot.texture_id if slot else None
    if texture_id is None:
        return None
    if int(texture_id) not in images:
        raise ValueError(
            f"plan 里 normal 角色有贴图 pathID={texture_id}，但 images 里没有它的派生图；"
            "先调 bake_normal_image(...) 并把返回的图名放进 images —— "
            "⛔ 不许拿原 DXT5nm 图顶替（那样法线会全错而不报错）"
        )
    return int(texture_id)


def _wire_normal(
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    principled: bpy.types.Node,
    material: bpy.types.Material,
    plan: kernel.MaterialPlan,
    baked_name: str,
    source_name: str | None,
) -> None:
    """派生 RGB 法线图 -> Normal Map(Strength=_NormalScale) -> Principled 的 Normal。

    ⛔ 接线形状**不在这里写** —— 它只有一份（`_wire_normal_image`），
    与「用户自己画的 PNG」那条路（`bind_user_map`）共用同一段代码。
    """
    # 按逻辑名找：_NormalScale / _NormalStrange / _Normal_Str（None = 不动 Blender 的默认 1.0）
    _wire_normal_image(nodes, links, principled, baked_name, plan.normal_scale)
    material[_PROP_NORMAL_SOURCE] = source_name or ""


def _wire_mask(
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    principled: bpy.types.Node,
    image_name: str,
    mask_links: list[kernel.ChannelLink],
    *,
    tag: str | None = None,
) -> str:
    """MaskMap 一张图 -> Separate Color；按 link 的 channel/invert 分到 Metallic / Roughness。

    G 通道（AO）那条 link 在内核里是 ignored=True（Principled 没有 AO 输入）—— 它进不了
    mask_links，**只有带 tag 的这条（用户自己接的图）**才额外给它找一个真落点：
    `Mix(MULTIPLY)` 乘进 Base Color（见 `_wire_ao_into_base`）。⛔ 内核那条口径一个字没改；
    「按件材质还原」那条路（tag=None）**照旧不接 AO**，读数口径不变。

    :param tag: 给我们自己建的那几颗节点打标签（重跑时只清自己那几颗）。
    :returns: AO 落点的人话（没接 AO 就是空串）。
    """
    tex = _image_node(nodes, image_name, (-800.0, -400.0))
    separate = nodes.new("ShaderNodeSeparateColor")
    separate.location = (-600.0, -400.0)
    if tag:
        _tag_node(tex, tag)
        _tag_node(separate, tag)
    links.new(tex.outputs["Color"], separate.inputs["Color"])
    for link in mask_links:
        target = principled.inputs[principled_input_name(link.blender_input)]
        source = _channel_output(tex, separate, link.channel)
        if source is None:  # pragma: no cover -- NODE_SHAPE 的 mask link 只有 R/A
            continue
        _link_maybe_invert(nodes, links, source, target, link.invert, tag=tag)
    if tag is None:
        return ""
    ao = _mask_ao_link()
    if ao is None:  # pragma: no cover -- kernel 里那条一直在
        return ""
    ao_source = _channel_output(tex, separate, ao.channel)
    if ao_source is None:  # pragma: no cover -- AO 恒为 G，SeparateColor 一定给得出
        return ""
    return _wire_ao_into_base(principled, nodes, links, ao_source)


def _apply_alpha_mode(material: bpy.types.Material, plan: kernel.MaterialPlan) -> None:
    """plan.alpha_mode -> 材质（**本机实测的 5.2 口径**）。

    Blender 5.2.1 实测（--factory-startup，逐个赋值再读回）：

    | 写进去 | 读回来 blend_method | 连带 surface_render_method |
    |---|---|---|
    | OPAQUE | **HASHED** | DITHERED |
    | CLIP   | **HASHED** | DITHERED |
    | HASHED | HASHED | DITHERED |
    | BLEND  | BLEND | BLENDED |

    blend_method 的 RNA 描述逐字是 "Blend Mode for Transparent Faces
    (Deprecated: use 'surface_render_method')" ⇒ 5.2 里 **CLIP / OPAQUE 已经没有独立档位**
    （都塌成 HASHED），真正管混合的是 surface_render_method；而 **alpha clip 的等价物是
    material.alpha_threshold**（RNA 描述："A pixel is rendered only if its alpha value is
    above this threshold"）。所以 CLIP 这一档：照工单写 blend_method，另外把
    alpha_threshold 写成 _AlphaCutoff —— 否则 0.5 的 cutoff 在 5.2 里**不生效也不报错**。
    """
    mode = plan.alpha_mode
    if mode == "BLEND":
        material.blend_method = "BLEND"  # 实测：同时把 surface_render_method 设成 BLENDED
    else:
        material.blend_method = "OPAQUE" if mode == "OPAQUE" else "CLIP"
        material.surface_render_method = "DITHERED"  # 5.2 的默认；显式写，别靠继承
    cutoff = plan.clip_threshold  # 按逻辑名找：_AlphaCutoff / _AlphaClipThreshold / _AlphaClipping
    if cutoff is not None:
        # ★ 2026-10-17：**不管哪一档都记原值**。因为 Alpha socket 现在不再承载 cutoff
        #   （见 `_apply_scalars` 里那条 skip），写回侧只能靠这个属性读回游戏真值；
        #   少了它，透明材质的 `_AlphaCutoff` 会被读成走 Alpha 的那颗数（静的改数据）。
        material[_PROP_ALPHA_CUTOFF] = float(cutoff)
        if mode == "CLIP":
            material.alpha_threshold = float(cutoff)
    material[_PROP_ALPHA_MODE] = plan.alpha_mode


def _math(nodes, operation: str, *, value: float | None = None, label: str = ""):
    '建一个 Math 节点（单输入常量走 `value`）；`label` 只给人看。'
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    if value is not None:
        node.inputs[1].default_value = float(value)
    node.label = label or operation
    return node


def _apply_transmission(principled: bpy.types.Node, plan: kernel.MaterialPlan) -> bool:
    r"""透射：`_TransmissionEnable` + 透明档 ⇒ Transmission Weight（+ `_Ior` 走标量那条）。

    ## 依据（HDRP 17 源码 + 全库读数）

    * `Lit.shader:168` `[ToggleUI] _TransmissionEnable = 1.0`；`LitDataIndividualLayer.hlsl:269`
      `transmissionMask = _TransmissionMask`（再乘 `_TransmissionMaskMap.r`，实测**没有**
      材质绑这张图）。
    * 全库实测（`_work/audit-full/materials-full.jsonl`，4 个主包）：**93 颗** `_SurfaceType=1` 且
      `_TransmissionEnable=1` 的 HDRP/Lit；93/93 的 `_TransmissionMask=1.0`；
      `_Ior` = 1.0(61) / 1.5(31) / 1.624(1)。
    * ⛔ `_TransmissionEnable` 的默认值就是 1.0（725 颗全有）⇒ **必须**用 alpha_mode=BLEND 当门，
      否则不透明材质会被一起变成玻璃。
    """
    if plan.alpha_mode != "BLEND":
        return False
    enable = plan.scalar("_TransmissionEnable")
    if enable is None or float(enable) < 0.5:
        return False
    weight = plan.scalar("_TransmissionMask")
    socket = principled.inputs.get(principled_input_name("Transmission Weight"))
    if socket is None:  # pragma: no cover -- 5.x 一定有时这个名字
        return False
    socket.default_value = max(0.0, min(1.0, 1.0 if weight is None else float(weight)))
    return True


def _apply_iridescence(
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    principled: bpy.types.Node,
    plan: kernel.MaterialPlan,
    images: Mapping[int, str],
) -> bool:
    r"""虹彩：`_IridescenceThickness(Map)` -> **Thin Film Thickness**（纳米），IQR 按 HDRP 公式推。

    ## 依据（HDRP 17 源码 + 全库读数）

    * `Lit.shader:68-72`：`_IridescenceThickness` Range(0,1) · `_IridescenceThicknessMap` ·
      `_IridescenceThicknessRemap` Vector(0,1) · `_IridescenceMask` Range(0,1)（+MaskMap）。
    * `LitDataIndividualLayer.hlsl:385-391`：
      `t = Remap.x + Remap.y * tex.r`（有图）或 `t = _IridescenceThickness`（无图）；mask 另乘。
    * `core/ShaderLibrary/BSDF.hlsl:483`：**「unit is micrometer … Mean 0.5 is 500nm」**
      ⇒ Blender 的 Thin Film Thickness 是**纳米** ⇒ `nm = t * 1000`。
    * 同一函数 `:493` `eta_2 = lerp(2.0, 1.0, thickness)` ⇒ 反向映射：`Thin Film IOR = 2 - t`。
    * 全库实测：**70 颗**带 `_MATERIAL_FEATURE_IRIDESCENCE`（门），其中 **68 颗**绑了厚度图；
      mask 图 0 颗；`_IridescenceThickness` 默认 1.0 出现在 724 颗里（⇒ 只看它会把整库都算成虹彩）。
    """
    if "_MATERIAL_FEATURE_IRIDESCENCE" not in frozenset(plan.keywords):
        return False
    thick = principled.inputs.get(principled_input_name("Thin Film Thickness"))
    if thick is None:  # pragma: no cover -- 5.2 实测有这个输入（见 _PRINCIPLED_NAMES）
        return False
    remap = plan.color("_IridescenceThicknessRemap") or (0.0, 1.0, 0.0, 0.0)
    low, high = float(remap[0]), float(remap[1])
    mask = plan.scalar("_IridescenceMask")
    mask = 1.0 if mask is None else max(0.0, min(1.0, float(mask)))
    slot = plan.slot_of(kernel.ROLE_IRIDESCENCE)
    image_name = (
        images.get(int(slot.texture_id))
        if slot is not None and slot.texture_id is not None
        else None
    )
    ior = principled.inputs.get(principled_input_name("Thin Film IOR"))
    if image_name is None:
        t = plan.scalar("_IridescenceThickness")
        t = 1.0 if t is None else float(t)
        thick.default_value = t * 1000.0 * mask
        if ior is not None:
            ior.default_value = max(1.0, 2.0 - t)
        return True
    tex = _image_node(nodes, image_name, (-1100.0, -420.0))
    # ⚠ R 通道要 SeparateColor 节点：`_channel_output` 只负责「从已有的分离节点上取口」，
    #   传 None 会直接返回 None（第一版就栽在这：图建出来了、厚度没接上，静默）。
    sep = nodes.new("ShaderNodeSeparateColor")
    sep.location = (-900.0, -420.0)
    links.new(tex.outputs["Color"], sep.inputs["Color"])
    red = _channel_output(tex, sep, "R")
    if red is None:  # pragma: no cover
        return False
    scaled = _math(nodes, "MULTIPLY_ADD", value=low, label="t = Remap.x + Remap.y*r")
    scaled.inputs[0].default_value = high  # 先乘 high（Remap.y）
    links.new(red, scaled.inputs[2])  # 再乘 r
    if mask < 1.0:
        masked = _math(nodes, "MULTIPLY", value=mask, label="* IridescenceMask")
        links.new(scaled.outputs[0], masked.inputs[0])
        t_out = masked.outputs[0]
    else:
        t_out = scaled.outputs[0]
    nm = _math(nodes, "MULTIPLY", value=1000.0, label="micrometer -> nm")
    links.new(t_out, nm.inputs[0])
    links.new(nm.outputs[0], thick)
    if ior is not None:
        eta = _math(nodes, "MULTIPLY_ADD", value=-1.0, label="IQR = 2 - t")
        eta.inputs[2].default_value = 2.0
        links.new(t_out, eta.inputs[0])
        links.new(eta.outputs[0], ior)
    return True

def _apply_solid_color(
    material: bpy.types.Material, principled: bpy.types.Node, plan: kernel.MaterialPlan
) -> bool:
    r"""**没有基色贴图**时，把游戏材质自己写的 `_BaseColor` 接成底色（透明档连 alpha 一起）。

    ## 为什么（2026-10-17 实测）

    玻璃 / 标点 / LOD 代理这批材质**没有基色贴图**：颜色与透明度全在 `_BaseColor` 里。
    全库实测（`_work/audit-full/materials-colors.jsonl`，4 个主包 3,111 颗）：
      * 玻璃类 **196 颗**没有基色贴图；`_BaseColor.a` 实测分布 0.043 / 0.051 / 0.078 / 0.086 /
        0.094 / 0.165 / 0.184 / 0.239 / 0.326 / 0.392 / 1.0 —— 例 `DLC2_UH60_glass` =
        `(0.537, 0.537, 0.467, 0.051)`、`DLC2_2Leopard3Glass` = `(0.608, 0.626, 0.821, 0.078)`。
      * 不接的话 Principled 停默认 `(0.8, 0.8, 0.8, 1.0)` ⇒ 玻璃看不出颜色、也不透。

    ⛔ 只动**没接基色贴图**的那种材质（有图的一条都不改：HDRP 的 `_BaseColor` 是**乘**在
    贴图上的 tint，接法要另外量，不在本次范围）。Alpha 只给 BLEND 档：OPAQUE/CLIP 的
    alpha 由 `_force_opaque_alpha` 压成 1.0，那是外观护栏的口径。
    """
    if any(link.role == kernel.ROLE_BASE_COLOR and link.wired for link in plan.links):
        return False
    rgba = plan.base_color_rgba()
    if rgba is None:
        return False
    changed = False
    socket = principled.inputs.get(principled_input_name("Base Color"))
    if socket is not None and not socket.is_linked:
        socket.default_value = (rgba[0], rgba[1], rgba[2], 1.0)
        changed = True
    if plan.alpha_mode == "BLEND":
        alpha = principled.inputs.get(principled_input_name("Alpha"))
        if alpha is not None and not alpha.is_linked:
            alpha.default_value = max(0.0, min(1.0, rgba[3]))
            changed = True
    if changed:
        material[_PROP_ALPHA_SOURCE] = "_BaseColor"
    return changed


def _force_opaque_alpha(
    material: bpy.types.Material, principled: bpy.types.Node, plan: kernel.MaterialPlan
) -> bool:
    """alpha_mode 是 OPAQUE / CLIP ⇒ Principled **Alpha 强制 1.0**（返回是否改过）。

    ## 为什么（2026-10-04 实测，读数在 reference_probe 的 JSON 里）

    _AlphaCutoff（游戏里是**阈值**，本作实测 0.5 / 0.713）被参数表写进 Principled 的
    **Alpha**（kernel 的 PARAM_TO_NODE_INPUT），于是 OPAQUE / CLIP 的表面变成**半透明混合**：
    unit 档覆盖像素里 **24.06%** 的 alpha < 250。两处受害：① 画面发暗；
    ② A5 并排比对拿 alpha 当**覆盖掩码**（② 的 raster_alpha_iou 预期不小于 0.98），
    抖动过的半透明会让它算不准。

    OPAQUE / CLIP 的 alpha 只该当**阈值**用 —— _apply_alpha_mode 已经把 _AlphaCutoff
    写进 material.alpha_threshold。透明那两档（HASHED / BLEND）**不动**：它们的 alpha
    是游戏真值，混合是它要的效果。

    ## 为什么要把原值记进材质属性

    Alpha 被压成 1.0 之后，read_material_params 会把 _AlphaCutoff 读成 1.0；
    W5 写回时就会把 1.0 写进游戏材质（= 静默改数据）。所以这里把**改写前**的值存进
    material[_PROP_ALPHA_CUTOFF]，read_material_params 优先读它 —— 往返仍逐值一致。
    """
    if plan.alpha_mode not in {"OPAQUE", "CLIP"}:
        return False
    socket = principled.inputs.get(principled_input_name("Alpha"))
    if socket is None or socket.is_linked:  # pragma: no cover -- NODE_SHAPE 不往 Alpha 接线
        return False
    original = float(socket.default_value)
    if abs(original - 1.0) <= 1e-9:
        return False
    socket.default_value = 1.0
    material[_PROP_ALPHA_CUTOFF] = original
    return True


def _mute_unwired_emission(principled: bpy.types.Node) -> bool:
    """没接自发光贴图 ⇒ 这个材质**不发光**（返回是否真的静音了）。

    ## 为什么（2026-10-04 实测；真凶在这里，不在灯光）

    _EmissiveIntensity 被参数表写进 Emission Strength，而 **Emission Color 只有有
    _EmissiveColorMap 时才接线**（kernel 的 NODE_SHAPE / emission_slots）；没图时它停在
    Blender 默认 **(1,1,1)** ⇒ 强度 × 白 = **整片纯白自发光**，材质外观归零。

    实测（unit 档 RU_T72B，同一场景、同一进程）：
    - 覆盖像素白化 **0.998061**、整帧只剩 **5** 种颜色；
    - 把太阳 energy 压到 0.001 仍白 **0.997645** ⇒ **与灯光无关**（所以不是曝光/曲线问题）；
    - 只把 Emission Strength 置 0（其余不动）：白化 **0**、颜色 1860+ 种。

    跟游戏侧对齐：_EmissiveIntensity 乘的是 _EmissiveColorMap，**没有图就没有自发光**。

    ## 为什么压 Emission Color，而不是把 Emission Strength 置 0

    Strength 是**游戏参数**的落点（read_material_params 的白名单按它读回）；置 0 会让
    「游戏里 _EmissiveIntensity 是多少」这个读数消失。Emission Color 是我们**没有**
    游戏来源的那个输入（只在有贴图时才接），压它最不丢信息 —— Strength 照样读回 1.0。
    """
    socket = principled.inputs.get(principled_input_name("Emission Color"))
    if socket is None or socket.is_linked:
        return False
    wanted = (0.0, 0.0, 0.0, 1.0)
    current = tuple(float(item) for item in socket.default_value)
    if all(abs(a - b) <= 1e-9 for a, b in zip(current, wanted, strict=True)):
        return False
    socket.default_value = wanted
    return True


def build_material_from_plan(
    name: str,
    plan: kernel.MaterialPlan,
    images: Mapping[int, str],
    *,
    normal_source: Mapping[int, str] | None = None,
) -> bpy.types.Material:
    """照 MaterialPlan 建一个 Blender 材质（形状 = kernel.NODE_SHAPE）。

    :param images: pathID -> Blender 图片名。**normal 角色必须给派生图名**
        （bake_normal_image 的返回），不是原 DXT5nm 图。
    :param normal_source: pathID -> 原 DXT5nm 图名（只用于 material_to_nodes 报出来）。
    :raises ValueError: plan 里 normal 角色有贴图、而 images 里没有对应的派生图。
    """
    material = bpy.data.materials.new(name)
    _build_into(material, plan, images, normal_source=normal_source)
    return material


def rebuild_material(
    material: bpy.types.Material,
    plan: kernel.MaterialPlan,
    images: Mapping[int, str],
    *,
    normal_source: Mapping[int, str] | None = None,
) -> bpy.types.Material:
    """**就地重建**既有材质的节点树（同一颗件跑两次结果一样）。

    ⛔ 不复用 build_material_from_plan 的"新建"那条：场景里那颗件是按**材质对象**挂槽的 ——
    新建一个同名材质会让它掉材质（用户看到的是"改了反而空了"）。
    """
    _build_into(material, plan, images, normal_source=normal_source)
    return material



#: 涂装层（`UnitV4_Camo` 的 desc='Camo'）用哪套 UV —— 导入器给 uv1 的层名
#: （见 importer.UV_LAYER_CAMO）。
CAMO_UV_LAYER = "UVMap_Camo"


#: 自发光（`_Emission`）走哪套 UV。
#:
#: ★ 2026-10-19 实测（用户现场：「为什么贴图是这个鬼样子？」）：`_Emission` 那张图**不是灯罩图**，
#: 是一条**纯色调色板**（白/红/绿/棕/橙/黄/淡紫…，**没有一个黑像素**）。用**主 UV（`UVMap`）**
#: 采样时，车体每个像素都在调色板上随机取色 ⇒ 整车被 `_EmissionStrange=12` 烤成粉/橙/绿。
#: 换成 `UVMap_Camo` 之后：车体落在调色板的黑区（不发光）、只有车灯那两块落在亮色上 ⇒ 正常。
#: 判据一行：`preview-fixed.png`（车灯亮、车体橄榄绿）对照整台发粉的旧渲染。
EMISSION_UV_LAYER = CAMO_UV_LAYER


def _use_emission_uv(nodes, links, tex) -> None:
    """给自发光贴图挂上它该用的 UV（`UVMap_Camo`）——理由见 `EMISSION_UV_LAYER` 那段实测。"""
    uv = nodes.new("ShaderNodeUVMap")
    uv.uv_map = EMISSION_UV_LAYER
    uv.location = (tex.location.x - 250.0, tex.location.y)
    links.new(uv.outputs["UV"], tex.inputs["Vector"])


def _apply_camo(
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    principled: bpy.types.Node,
    plan: kernel.MaterialPlan,
    images: dict[int, str],
    material: bpy.types.Material,
) -> bool:
    """涂装层：Camo 图（`UVMap_Camo` × `_CamoTiling` + `_OffsetX/_OffsetY`）按实测权重混进基色。

    ⛔ **这不是 shader 的原式**：Camo 层的合成式没钉死（见 `kernel.CAMO_MIX_WEIGHT` 的说明：
    实测只到「参与约 24% 权重、主色跟 Albedo 走、不是相乘也不是整层覆盖」，diffuse 正控没通）。
    所以这里做成**显式参数**：权重写进材质属性 `bamod_camo_weight`（默认 0.24），可在面板上改。
    返回是否接上了。
    """
    slot = plan.slot_of(kernel.ROLE_CAMO)
    if slot is None or slot.texture_id is None:
        return False
    image_name = images.get(int(slot.texture_id))
    if image_name is None:
        return False
    tex = _image_node(nodes, image_name, (-900.0, -700.0))
    # 自己的一套 UV + 平铺/偏移（实测逐套皮肤不同：2.0/-0.004 · 1.2/-0.2 · 2.0/0.6…）
    uv = nodes.new("ShaderNodeUVMap")
    uv.uv_map = CAMO_UV_LAYER
    uv.location = (-1300.0, -700.0)
    mapping = nodes.new("ShaderNodeMapping")
    mapping.vector_type = "POINT"
    mapping.location = (-1100.0, -700.0)
    tiling = plan.color("_CamoTiling")
    mapping.inputs["Scale"].default_value = (
        (tiling[0], tiling[1], 1.0) if tiling is not None else (1.0, 1.0, 1.0)
    )
    offset_x = plan.scalar("_OffsetX") or 0.0
    offset_y = plan.scalar("_OffsetY") or 0.0
    mapping.inputs["Location"].default_value = (offset_x, offset_y, 0.0)
    links.new(uv.outputs["UV"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
    target = principled.inputs[principled_input_name("Base Color")]
    base_source = target.links[0].from_socket if target.is_linked else None
    if target.is_linked:
        links.remove(target.links[0])
    weight = float(material.get("bamod_camo_weight", kernel.CAMO_MIX_WEIGHT))
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MIX"
    mix.label = f"Camo 层（近似权重 {weight:.2f}）"
    mix.inputs["Factor"].default_value = weight
    a_in = next(s for s in mix.inputs if s.name == "A" and s.type == "RGBA")
    b_in = next(s for s in mix.inputs if s.name == "B" and s.type == "RGBA")
    result = next(s for s in mix.outputs if s.name == "Result" and s.type == "RGBA")
    if base_source is not None:
        links.new(base_source, a_in)
    else:
        a_in.default_value = tuple(target.default_value)
    links.new(tex.outputs["Color"], b_in)
    links.new(result, target)
    material["bamod_camo_weight"] = weight
    return True

def _build_into(
    material: bpy.types.Material,
    plan: kernel.MaterialPlan,
    images: Mapping[int, str],
    *,
    normal_source: Mapping[int, str] | None = None,
) -> bpy.types.Node:
    """把 plan 施工到 material 上（**先清空节点树** ⇒ 幂等）；返回 Principled 节点。

    :raises ValueError: plan 里 normal 角色有贴图、而 images 里没有对应的派生图。
    """
    normal_id = _wired_normal_texture_id(plan, images)  # 先校验：缺派生图要在建之前就炸

    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()  # ★ 幂等：同一 plan ＋ 同一批图 ⇒ 同一棵树（节点名也不会累积 .001）
    nodes, links = tree.nodes, tree.links
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    _apply_scalars(principled, plan, images)
    _record_scalar_names(material, plan)  # 读回时的白名单（见 read_material_params）

    mask_links = [link for link in plan.links if link.role == kernel.ROLE_MASK and link.wired]
    mask_slot = plan.slot_of(kernel.ROLE_MASK)
    mask_image = (
        images.get(int(mask_slot.texture_id))
        if mask_slot is not None and mask_slot.texture_id is not None
        else None
    )
    if mask_links and mask_image:
        _wire_mask(nodes, links, principled, mask_image, mask_links)

    # 基色 / 自发光：整张颜色直连（channel 恒为 None）。
    # ⛔ 虹彩厚度那条不进这个循环：它要过 remap 与 ×1000（见 `_apply_iridescence`）。
    for link in plan.links:
        if not link.wired or link.role in (
            kernel.ROLE_MASK,
            kernel.ROLE_NORMAL,
            kernel.ROLE_IRIDESCENCE,
        ):
            continue
        slot = plan.slot_of(link.role)
        if slot is None or slot.texture_id is None:
            continue
        image_name = images.get(int(slot.texture_id))
        if image_name is None:
            continue  # 解码失败已由调用方记进 report.errors（W3 的兜底）
        tex = _image_node(nodes, image_name, (-600.0, 200.0))
        if link.role == kernel.ROLE_EMISSION:
            _use_emission_uv(nodes, links, tex)
        source = _channel_output(tex, None, link.channel)
        if source is None:  # pragma: no cover
            continue
        target = principled.inputs[principled_input_name(link.blender_input)]
        tint = plan.base_color_rgba() if link.role == kernel.ROLE_BASE_COLOR else None
        if tint is None:
            _link_maybe_invert(nodes, links, source, target, link.invert)
            continue
        # ★ 2026-10-17：`_BaseColorMap` × `_BaseColor`（HDRP 是相乘）：实测 619 颗有基色贴图，
        #   其中 67 颗 tint 不是纯白（例 `Grass_Wild_L4` = (0.802, 0.786, 0.526)）。
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        mix.label = "BaseColorMap x _BaseColor"
        mix.inputs["Factor"].default_value = 1.0
        a_in = next(s for s in mix.inputs if s.name == "A" and s.type == "RGBA")
        b_in = next(s for s in mix.inputs if s.name == "B" and s.type == "RGBA")
        mixed = next(s for s in mix.outputs if s.name == "Result" and s.type == "RGBA")
        links.new(source, a_in)
        b_in.default_value = (tint[0], tint[1], tint[2], 1.0)
        links.new(mixed, target)
        # ★ 2026-10-17 第三修（用户报 Yak130 座舱）：**CLIP / BLEND 两档都要接贴图 alpha**。
        #   HDRP 原式（`LitData.hlsl:232-234`）：
        #     alphaValue = lerp(_AlphaRemapMin, _AlphaRemapMax, alphaTex) * _BaseColor.a
        #   旧版只给 BLEND 接、CLIP 档强制 Alpha=1.0 ⇒ **`_ALPHATEST_ON` 的挖洞全没了**：
        #   Yak130 机身那块「除了白条纹全是透明」的座舱开口在 Blender 里被画成黑壳。
        alpha = principled.inputs.get(principled_input_name("Alpha"))
        if plan.alpha_mode in {"CLIP", "BLEND"} and alpha is not None and not alpha.is_linked:
            tex_alpha = tex.outputs.get("Alpha")
            if tex_alpha is not None:
                source = tex_alpha
                low = plan.scalar("_AlphaRemapMin")
                high = plan.scalar("_AlphaRemapMax")
                low = 0.0 if low is None else float(low)
                high = 1.0 if high is None else float(high)
                if abs(low) > 1e-6 or abs(high - 1.0) > 1e-6:
                    remap = _math(nodes, "MULTIPLY_ADD", value=low, label="alpha remap")
                    remap.inputs[0].default_value = high - low
                    links.new(source, remap.inputs[2])
                    source = remap.outputs[0]
                if abs(tint[3] - 1.0) > 1e-6:
                    mul = _math(nodes, "MULTIPLY", value=tint[3], label="alpha x _BaseColor.a")
                    links.new(source, mul.inputs[0])
                    source = mul.outputs[0]
                if plan.alpha_mode == "CLIP":
                    # HDRP 是 alpha test（`GENERIC_ALPHA_TEST(alphaValue, _AlphaCutoff)`，硬裁），
                    # 而 `material.alpha_threshold` 只对 EEVEE 生效、Cycles 直接忽略 ⇒ 这里用
                    # GREATER_THAN 做成 0/1 的硬裁，两个引擎都对得上（实测 Yak130 座舱开口）。
                    cutoff = plan.clip_threshold
                    cutoff = 0.5 if cutoff is None else float(cutoff)
                    hard = _math(nodes, "GREATER_THAN", value=cutoff, label="alpha test")
                    links.new(source, hard.inputs[0])
                    source = hard.outputs[0]
                links.new(source, alpha)

    # ★ 2026-10-17：没基色贴图的材质（玻璃 / 标点 / LOD 代理）用游戏自己的 `_BaseColor`。
    _apply_solid_color(material, principled, plan)
    # ★ 2026-10-17：涂装层（`UnitV4_Camo` 的 desc='Camo'）—— Yak130 皮肤 1/2/3 的差别只在这层。
    _apply_camo(nodes, links, principled, plan, images, material)
    # ★ 2026-10-17 第二轮（「能接的都接上」）：透射 / 虹彩。
    _apply_transmission(principled, plan)
    _apply_iridescence(nodes, links, principled, plan, images)

    # 2026-10-04 的两条**外观护栏**（Lead 授权的最小修；起因与读数见各自 docstring）。
    # 必须在上面那圈贴图连线**之后**：自发光那条护栏要看 Emission Color 到底接没接图。
    _force_opaque_alpha(material, principled, plan)
    _mute_unwired_emission(principled)

    if normal_id is not None:
        _wire_normal(
            nodes,
            links,
            principled,
            material,
            plan,
            images[normal_id],
            (normal_source or {}).get(normal_id),
        )

    _apply_alpha_mode(material, plan)
    return principled


# --------------------------------------------------------------------------- #
# 按件的材质还原：包里读贴图 -> bpy.data.images -> NODE_SHAPE 三路
# --------------------------------------------------------------------------- #
#: ⛔ 诚实边界（面板 / 读数 / 摘要里必须一起显示出来，别只放在代码注释里）。
PART_APPROX_NOTE = (
    "近似（⛔ 不是游戏观感）：游戏 shader 是随包携带的编译件、Granite VT 在 Blender 没有对应物；"
    "这里只还原“哪张贴图上哪一路”。遮蔽 / 倒影 / 条纹三类只有用户能判（AGENTS §0.12.1）。"
)

#: 摘要里逐路的显示顺序 ＋ 人话名（⛔ 不改 kernel 那边的角色常量）。
_ROUTE_ORDER: tuple[str, ...] = (
    kernel.ROLE_BASE_COLOR,
    kernel.ROLE_NORMAL,
    kernel.ROLE_MASK,
    kernel.ROLE_EMISSION,
)
_ROUTE_LABEL: Mapping[str, str] = {
    kernel.ROLE_BASE_COLOR: "基色",
    kernel.ROLE_NORMAL: "法线",
    kernel.ROLE_MASK: "MaskMap",
    kernel.ROLE_EMISSION: "自发光",
}
#: 每一路的**通道对应**（读数行里印出来；口径 = kernel.NODE_SHAPE，这里只是显示）。
_ROUTE_CHANNEL: Mapping[str, str] = {
    kernel.ROLE_BASE_COLOR: "整张颜色 -> Principled.Base Color",
    kernel.ROLE_NORMAL: "派生 RGB 法线 -> Normal Map(Strength=_NormalScale) -> Principled.Normal",
    kernel.ROLE_MASK: "R->Metallic · A->Invert->Roughness · G(AO) 只标注不接线",
    kernel.ROLE_EMISSION: "整张颜色 -> Principled.Emission Color（只有真绑了自发光图才接）",
}


@dataclass(frozen=True, slots=True)
class RouteReadout:
    """一路**真接上**的读数（图名 / 尺寸 / 颜色空间 / 通道对应）。"""

    role: str
    slot: str
    texture_id: int
    image: str
    source: str
    width: int
    height: int
    colorspace: str
    fmt: str = ""
    reused: bool = False

    def line(self) -> str:
        tag = "复用" if self.reused else "新建"
        return (
            f"{_ROUTE_LABEL.get(self.role, self.role)}={self.slot} -> {self.image}"
            f"（{self.width}x{self.height} · {self.fmt or '?'} · {self.colorspace} · 图{tag}）"
            f"｜{_ROUTE_CHANNEL.get(self.role, '')}"
        )


@dataclass(frozen=True, slots=True)
class MaterialReadout:
    """一颗材质的还原读数：接上哪几路 / 哪几路没接（逐条给理由）。"""

    material_id: int
    game_name: str
    shader: str
    blender_name: str
    coverage: str
    routes: tuple[RouteReadout, ...] = ()
    missing: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def line(self) -> str:
        wired = (
            "、".join(
                f"{_ROUTE_LABEL.get(item.role, item.role)}({item.slot})" for item in self.routes
            )
            or "（一路都没接）"
        )
        return (
            f"{self.blender_name} ｜ 游戏名 {self.game_name} ｜ shader {self.shader or '?'}"
            f" ｜ 还原度 {self.coverage} ｜ 接上：{wired} ｜ 没接 {len(self.missing)} 路"
        )


@dataclass
class RestoreReport:
    """一次「按件材质还原」的读数（面板画摘要、探针做判据都用它）。"""

    key: str = ""
    bundle: str = ""
    asset_path: str = ""
    materials: list[MaterialReadout] = field(default_factory=list)
    images_new: list[str] = field(default_factory=list)
    images_reused: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def wired(self) -> dict[str, int]:
        """角色 -> **有几颗材质**真的接上了这一路（不是有几条线）。"""
        out: dict[str, int] = {}
        for item in self.materials:
            for role in sorted({route.role for route in item.routes}):
                out[role] = out.get(role, 0) + 1
        return out

    def summary(self) -> str:
        """一句人话：材质几个 / 真接上几路 / 没接几路 / 图几张（新 & 复用）。"""
        wired = self.wired()
        labels = "、".join(
            f"{_ROUTE_LABEL[role]}{wired[role]}" for role in _ROUTE_ORDER if wired.get(role)
        )
        miss = sum(len(item.missing) for item in self.materials)
        total = len(self.images_new) + len(self.images_reused)
        tail = f" · 错误 {len(self.errors)} 条" if self.errors else ""
        return (
            f"{self.key or '这个件'}：材质 {len(self.materials)} 个"
            f" / 真接上 {sum(wired.values())} 路（{labels or '一路都没有'}）"
            f" / 没接 {miss} 路 / 图 {total} 张"
            f"（新 {len(self.images_new)} · 复用 {len(self.images_reused)}）"
            f" · {self.seconds:.1f}s{tail}"
        )

    def lines(self) -> list[str]:
        """读数行（第 1 行 = 摘要；面板只画摘要，明细进「看详情」）。"""
        out = [self.summary()]
        for item in self.materials:
            out.append(item.line())
            out.extend("    " + route.line() for route in item.routes)
            out.extend("    ⛔ " + reason for reason in item.missing)
            out.extend("    · " + note for note in item.notes)
        out.extend("⛔ " + error for error in self.errors)
        out.append(PART_APPROX_NOTE)
        return out

    def fingerprint(self) -> str:
        """**幂等判据**：节点树 ＋ 接线 ＋ 每路（槽/图/尺寸/colorspace）规范化后的 sha256[:16]。"""
        rows = []
        for item in self.materials:
            material = bpy.data.materials.get(item.blender_name)
            rows.append(
                {
                    "material": item.blender_name,
                    "tree": material_to_nodes(material) if material is not None else {},
                    "routes": [
                        [r.role, r.slot, r.texture_id, r.image, r.colorspace, r.width, r.height]
                        for r in item.routes
                    ],
                }
            )
        blob = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def part_summary(report: RestoreReport) -> str:
    """**一句人话**：哪几路真接上了、哪几路没接（面板画它当摘要；明细走 report.lines()）。"""
    return report.summary()


def shader_prop_descs(source: object, material_id: int) -> dict[str, str]:
    """材质 -> **shader 自己写的**槽位描述 `{槽名: m_Description}`。

    读法：材质 typetree 的 `m_Shader` PPtr → shader 的 `m_ParsedForm.m_PropInfo.m_Props[]`
    （与 `texture_install.color_layers` 同一份口径 —— 认颜色层只认 desc 里的 'Albedo' / 'Camo'）。
    ⛔ 槽名里的 hash（`Layer_A97CDC25`）随 shader 变，**desc 才是契约**。

    本机实测（units 包）：`Shader Graphs/UnitV4_Camo` 的 85 个 prop 里 4 条带 desc ——
    `Layer_A97CDC25`=Albedo · `Layer_A97CDC25_1`=Camo · `Layer_8D60137F`=Normal ·
    `Layer_4C4561B0`=Mask。

    :raises Exception: 读不到就抛（调用方决定是记账还是跳过）—— ⛔ 不把"读不到"当成"没有颜色层"。
    """
    tree = source.read_typetree(int(material_id))
    shader_id = int((tree.get("m_Shader") or {}).get("m_PathID", 0) or 0)
    if not shader_id:
        return {}
    parsed = (source.read_typetree(shader_id) or {}).get("m_ParsedForm") or {}
    props = (parsed.get("m_PropInfo") or {}).get("m_Props") or []
    out: dict[str, str] = {}
    for item in props:
        name = str(item.get("m_Name") or "")
        if name:
            out[name] = str(item.get("m_Description") or "")
    return out


def _wired_roles(plan: kernel.MaterialPlan) -> list[str]:
    """plan 里**真的要接线**的角色（按 NODE_SHAPE 的顺序，去重）。"""
    seen: list[str] = []
    for link in plan.links:
        if link.wired and link.role not in seen:
            seen.append(link.role)
    return seen


def _drop_role(plan: kernel.MaterialPlan, role: str) -> kernel.MaterialPlan:
    """摘掉某个角色的连线（只有**解码失败**时才用：⛔ 不许拿一张没解出来的图去接）。"""
    return replace(plan, links=tuple(link for link in plan.links if link.role != role))


def _ensure_image(
    name: str,
    width: int,
    height: int,
    rgba_topdown: bytes,
    color_space: int,
    *,
    reuse: bool = True,
) -> tuple[bpy.types.Image, bool]:
    """名字 -> 图（**幂等**）：同名同尺寸且 reuse ⇒ 直接返回原图，**不重写像素**。"""
    existing = bpy.data.images.get(name)
    if existing is not None:
        if reuse and tuple(int(v) for v in existing.size) == (int(width), int(height)):
            wanted = colorspace_of(int(color_space))
            if existing.colorspace_settings.name != wanted:
                existing.colorspace_settings.name = wanted  # ⚠ 只改标签，不动像素
            return existing, True
        bpy.data.images.remove(existing)  # 尺寸变了 ⇒ 换掉（⛔ 不静默拿错尺寸那张）
    return _new_image(name, int(width), int(height), rgba_topdown, color_space), False


def _ensure_baked_normal(
    texture_id: int, source_image_name: str, rgba: bytes, width: int, height: int
) -> tuple[bpy.types.Image, bool]:
    """派生法线图（DXT5nm -> RGB）＋**是不是复用的** —— 同名同尺寸就复用（幂等的另一半）。

    ⛔ z 的重建仍然只走 `bake_normal_image` → `normals.dxt5nm_to_rgb_normal`（这里没有公式副本）。
    ⛔ 跨会话也要幂等：`_BAKED_NORMAL` 那个缓存只活在进程里，重启之后靠**名字+尺寸**认回来。
    """
    name = f"{source_image_name}{NORMAL_RGB_SUFFIX}"
    existing = bpy.data.images.get(name)
    if existing is not None and tuple(int(v) for v in existing.size) == (int(width), int(height)):
        _BAKED_NORMAL.setdefault(int(texture_id), name)
        return existing, True
    return bake_normal_image(int(texture_id), source_image_name, rgba, width, height), False


def _decode_part_texture(
    source: object,
    texture_id: int,
    *,
    key: str,
    reuse: bool,
    decoded: dict[int, tuple[str, object, bytes, bool]],
    out: RestoreReport,
) -> tuple[str, object, bytes, bool]:
    """贴图 pathID -> (Blender 图名, TextureInfo, 原 RGBA(上到下), 是不是复用的)；同一张只解一次。

    ⛔ 颜色空间必须在写像素**之前**设（`_new_image` 里那一条，见它的 docstring）；复用那条路
    只改标签不动像素。
    """
    cached = decoded.get(int(texture_id))
    if cached is not None:
        return cached
    info = source.read_texture(int(texture_id))
    rgba = source.decode_texture_rgba(int(texture_id))
    name = f"{key}__{info.name}" if key else str(info.name)
    image, reused = _ensure_image(
        name, int(info.width), int(info.height), rgba, int(info.color_space), reuse=reuse
    )
    expected = colorspace_of(int(info.color_space))
    if image.colorspace_settings.name != expected:
        out.errors.append(
            f"贴图 {info.name}：颜色空间是 {image.colorspace_settings.name}，"
            f"按 m_ColorSpace={info.color_space} 应当是 {expected}"
        )
    if reused:
        if image.name not in out.images_reused:
            out.images_reused.append(image.name)
    elif image.name not in out.images_new:
        out.images_new.append(image.name)
    entry = (image.name, info, rgba, reused)
    decoded[int(texture_id)] = entry
    return entry


def _restore_one(
    source: object,
    material_id: int,
    entry: Mapping[str, object],
    *,
    key: str,
    reuse: bool,
    decoded: dict[int, tuple[str, object, bytes, bool]],
    out: RestoreReport,
) -> MaterialReadout:
    """一颗材质：读 desc → 出计划（kernel）→ 解图 → 建/重建节点树 → 读数。"""
    game_name = str(entry.get("name") or material_id)
    shader = str(entry.get("shader") or "")
    descs: dict[str, str] = {}
    table = kernel.table_for(shader)
    if table is not None and table.base_color_descs:
        # ★ 只有 M4（UnitV4_Camo）把「哪个槽算颜色层」写在 **shader 自己写的 desc** 里。
        #   ⛔ 不读它 ⇒ 那批车（DLC2）一个颜色槽都认不出。实测 RU_BMD_Kornet 的
        #   `RU_BMD4_Armor 1`：不给 desc ⇒ 一路都不接（视口里一块平的灰面）；
        #   给了 desc ⇒ `Layer_A97CDC25`(Albedo) 接上基色。
        try:
            descs = shader_prop_descs(source, material_id)
        except Exception as exc:  # noqa: BLE001 -- 读不到就说读不到，⛔ 不当成「没有颜色层」
            out.errors.append(
                f"{game_name}：shader 的槽位描述读不到（{type(exc).__name__}: {exc}）"
            )
    plan = kernel.plan_material(entry, prop_descs=descs or None)
    wired_roles = set(_wired_roles(plan))

    images: dict[int, str] = {}
    normal_source: dict[int, str] = {}
    routes: list[RouteReadout] = []
    missing: list[str] = []
    for role in _ROUTE_ORDER:
        label = _ROUTE_LABEL.get(role, role)
        binding = plan.slot_of(role)
        if binding is None:
            missing.append(
                f"{label}：包里没有这一路（{shader or 'shader 名空'} 里没这个槽；"
                "VT 层在静态资产里根本没有对应槽）"
            )
            continue
        if binding.texture_id is None:
            missing.append(f"{label}：槽 {binding.slot} 是空槽（包里没赋图）")
            continue
        texture_id = int(binding.texture_id)
        if role not in wired_roles:
            # Unity 里「槽绑着图」≠「这个变体用这张图」：开关没开的那几路**连图都不必解**
            # （省一张 2048² 的内存，也不留没人用的图数据块）。
            missing.append(
                f"{label}：槽 {binding.slot} 有图（pathID={texture_id}），"
                "但 shader 变体的开关没开 —— 按游戏口径不接（连图都不建）"
            )
            continue
        try:
            image_name, info, rgba, reused = _decode_part_texture(
                source, texture_id, key=key, reuse=reuse, decoded=decoded, out=out
            )
        except Exception as exc:  # noqa: BLE001 -- 单张贴图解不出来不该废掉整颗件，但要点名
            out.errors.append(
                f"{game_name} / {label}：贴图 pathID={texture_id} 解码失败"
                f"（{type(exc).__name__}: {exc}）"
            )
            missing.append(f"{label}：贴图 pathID={texture_id} 解码失败")
            plan = _drop_role(plan, role)
            continue
        if role == kernel.ROLE_NORMAL:
            baked, baked_reused = _ensure_baked_normal(
                texture_id, image_name, rgba, info.width, info.height
            )
            images[texture_id] = baked.name
            normal_source[texture_id] = image_name
            # 派生图也是我们建的图 —— 记账要带上它，否则摘要里的「图 N 张」比场景少一张
            if baked_reused:
                if baked.name not in out.images_reused:
                    out.images_reused.append(baked.name)
            elif baked.name not in out.images_new:
                out.images_new.append(baked.name)
        else:
            images[texture_id] = image_name
        image = bpy.data.images[images[texture_id]]
        routes.append(
            RouteReadout(
                role=role,
                slot=str(binding.slot),
                texture_id=texture_id,
                image=image.name,
                source=image_name,
                width=int(info.width),
                height=int(info.height),
                colorspace=str(image.colorspace_settings.name),
                fmt=str(kernel_textures.format_name(int(getattr(info, "format", 0) or 0))),
                reused=bool(reused),
            )
        )

    name = f"{key}__{game_name}" if key else game_name
    existing = bpy.data.materials.get(name)
    if existing is not None:
        material = rebuild_material(existing, plan, images, normal_source=normal_source)
    else:
        material = build_material_from_plan(name, plan, images, normal_source=normal_source)
    if material.users == 0:
        material.use_fake_user = True

    notes = list(plan.notes)
    desc_slots = tuple(getattr(plan, "desc_color_slots", ()) or ())
    if len(desc_slots) > 1:
        notes.append(
            f"颜色层有 {len(desc_slots)} 个槽（{'、'.join(desc_slots)}），节点树只接第一路 —— "
            "NODE_SHAPE 只有一条 base_color（其余颜色层没接：近似）"
        )
    return MaterialReadout(
        material_id=int(material_id),
        game_name=game_name,
        shader=shader,
        blender_name=material.name,
        coverage=str(plan.coverage),
        routes=tuple(routes),
        missing=tuple(missing),
        notes=tuple(notes),
    )


def restore_part(
    source: object,
    asset_path: str,
    *,
    key: str = "",
    reuse: bool = True,
    report: RestoreReport | None = None,
) -> RestoreReport:
    """**按件的材质还原**（② 改 · 材质段 / 观感那格的能力入口）。

    一件（prefab）走一遍：包里读贴图 -> `bpy.data.images`（带正确 colorspace）-> 照
    `kernel.NODE_SHAPE` 把 BaseColor / Normal / MaskMap（＋自发光）接进 Principled。

    :param source: 已经开着的读源（`asset_kernel.unitypy_source.AssetSource`）；⛔ 本函数不开关包。
        ⚠ **开包很贵**（本机 units 包一次 ≈ 10 GB 峰值内存）⇒ **串行**这件事归调用方：
        探针用 `_work/take/heavy_lock.py` 那把锁；面板自己那把由面板定（⛔ 两处都开 = 换页）。
    :param asset_path: 容器里的地址（prefab 的路径键）。
    :param key: 名字前缀（① 拿 落的单位键）：图与材质名 = `f'{key}__{原名}'`。
    :param reuse: True（默认）= 同名同尺寸的图直接复用 ⇒ **同一颗件跑两次结果一样**。
    :param report: 传一个进来可以接着上次的账（面板分步跑时用）。
    :returns: `RestoreReport`（`.summary()` 一句人话、`.lines()` 明细、`.fingerprint()` 幂等判据）。
    """
    started = time.time()
    out = report if report is not None else RestoreReport()
    if key:
        out.key = key
    out.asset_path = str(asset_path)
    out.bundle = str(getattr(source, "path", "") or out.bundle)
    _nodes, entries = source.read_prefab_nodes(asset_path)
    decoded: dict[int, tuple[str, object, bytes, bool]] = {}
    for material_id, entry in sorted(entries.items()):
        out.materials.append(
            _restore_one(
                source,
                material_id,
                entry,
                key=key,
                reuse=reuse,
                decoded=decoded,
                out=out,
            )
        )
    out.seconds = time.time() - started
    return out


def restore_bundle(
    unit_session, asset_path: str = "", *, key: str = "", reuse: bool = True
):
    """**先确认这个会话开过一次包**，再 `restore_part`（⛔ 本模块不再自己开关包）。

    ★ 2026-10-18：开包**只有一处**（`session.UnitSession`）。旧签名吃 `bundle` 路径、自己
    拿 `unitypy_source` 开一次 —— 那等于插件里的**第二条开包路**（同一件事两份实现，
    `materials` 与 `importer` 各推一遍 ⇒ 读数会分叉）。现在第一参数是会话：
    `session.session_for(key, bundle_path=bundle, asset_path=asset_path)`。

    :param unit_session: `session.UnitSession`（`open()` 缓存那次 `AssetSource`）；
        ⚠ 生命周期归调用方 —— 本函数**不关**它（3.4 GB 的包 ≈ 10.9 GB 内存）。
    :param asset_path: 容器里的地址（prefab 的路径键）；空则用会话里那个。
    """
    if asset_path and not str(getattr(unit_session, "asset_path", "") or ""):
        unit_session.asset_path = str(asset_path)
    # 只开包、⛔ 不建图：`restore_part` 自己 `read_prefab_nodes(asset_path)`，那张图纯属白花时间。
    # 「只开一次包」照样成立（`open()` 与 `load_graph()` 共用同一个 `_source`）。
    unit_session.open()
    target = str(asset_path or getattr(unit_session, "asset_path", "") or "")
    return restore_part(unit_session.source, target, key=key, reuse=reuse)


def route_plan_lines(
    entries: Mapping[int, Mapping[str, object]],
    *,
    descs_of: Mapping[int, Mapping[str, str]] | None = None,
) -> list[str]:
    """干跑：**不读包、不建图**，只算每颗材质哪几路会接 / 哪几路不接（面板 check·plan 用）。

    :param descs_of: 材质 pathID -> shader 自写的 `{槽名: desc}`（UnitV4_Camo 认颜色层要用它；
        ⛔ 不给时那批车会如实报「一路都没接」—— 这不是 bug，是没读 desc）。
    """
    out: list[str] = []
    for material_id, entry in sorted(entries.items()):
        plan = kernel.plan_material(
            entry, prop_descs=dict((descs_of or {}).get(int(material_id)) or {}) or None
        )
        wired = _wired_roles(plan)
        head = "、".join(
            f"{_ROUTE_LABEL.get(role, role)}({plan.slot_of(role).slot})" for role in wired
        )
        blanks: list[str] = []
        for role in _ROUTE_ORDER:
            if role in wired:
                continue
            label = _ROUTE_LABEL.get(role, role)
            binding = plan.slot_of(role)
            if binding is None:
                blanks.append(f"{label}=没这个槽")
            elif binding.texture_id is None:
                blanks.append(f"{label}={binding.slot} 空槽")
            else:
                blanks.append(f"{label}={binding.slot} 有图但开关没开")
        out.append(
            f"{entry.get('name') or material_id} ｜ {entry.get('shader') or '?'} ｜ "
            f"还原度 {plan.coverage} ｜ 接：{head or '（一路都没接）'} ｜ "
            f"不接：{'、'.join(blanks) or '（没有）'}"
        )
    return out


# --------------------------------------------------------------------------- #
# 读回来
# --------------------------------------------------------------------------- #
def _principled_of(mat: bpy.types.Material) -> bpy.types.Node:
    tree = mat.node_tree
    node = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if node is None:
        raise ValueError(f"材质 {mat.name!r} 没有 Principled 节点")
    return node


def _alpha_mode_of(blend_method: str) -> str:
    """material.blend_method -> 三档 alpha_mode（HASHED / OPAQUE 都算 OPAQUE）。"""
    return blend_method if blend_method in {"CLIP", "BLEND"} else "OPAQUE"


def _image_name_of(node: bpy.types.Node) -> str | None:
    """TexImage 节点的图名；非 TexImage 或没挂图都给 None（material_to_nodes 用）。"""
    if node.bl_idname == "ShaderNodeTexImage" and node.image is not None:
        return node.image.name
    return None


def _images_by_role(tree: bpy.types.NodeTree, principled: bpy.types.Node) -> dict[str, str | None]:
    """按**实际连线**反查四个角色各用了哪张图（不是记在旁边的账）。"""
    out: dict[str, str | None] = {
        kernel.ROLE_BASE_COLOR: None,
        kernel.ROLE_NORMAL: None,
        kernel.ROLE_MASK: None,
        kernel.ROLE_EMISSION: None,
    }
    base_input = principled_input_name("Base Color")
    emission_input = principled_input_name("Emission Color")
    # ★ AO 是**乘进 Base Color** 的（见 _wire_ao_into_base）：那一路的原始基色图接到的是
    #   Mix 的 B，不是 Principled —— 不认这颗 Mix 就会把「基色」读成 None（**假读数**）。
    mix_into_base: set[str] = set()
    for link in tree.links:
        node = link.from_node
        if (
            node.bl_idname == "ShaderNodeMix"
            and str(node.get(_MAP_TAG) or "") == kernel.ROLE_MASK
            and link.to_node.name == principled.name
            and link.to_socket.name == base_input
        ):
            mix_into_base.add(node.name)
    for link in tree.links:
        node = link.from_node
        # ⚠ 别用 "link.to_node is principled"：Blender 每次访问 RNA 都新建包装对象，
        # is 会假空（实测：base_color / emission 全读成 None），一律按 name 比。
        if node.bl_idname != "ShaderNodeTexImage" or node.image is None:
            continue
        if link.to_node.name == principled.name:
            if link.to_socket.name == base_input:
                out[kernel.ROLE_BASE_COLOR] = node.image.name
            elif link.to_socket.name == emission_input:
                out[kernel.ROLE_EMISSION] = node.image.name
        elif link.to_node.bl_idname == "ShaderNodeNormalMap":
            out[kernel.ROLE_NORMAL] = node.image.name
        elif link.to_node.bl_idname == "ShaderNodeSeparateColor":
            out[kernel.ROLE_MASK] = node.image.name
        elif link.to_node.name in mix_into_base and link.to_socket.name == "B":
            out[kernel.ROLE_BASE_COLOR] = node.image.name
    return out


def material_to_nodes(mat: bpy.types.Material) -> dict[str, object]:
    """节点树的可断言描述（形状冻结 —— Lead 的用例逐条断言它）。

    返回（全部值 JSON 友好）::

        {
          "nodes": [{"name": 节点名, "type": "ShaderNodeTexImage"|...,
                     "image": 图片名或 None, "defaults": {数值型输入名: float}}, ...],
          "links": [[from_name, from_type, from_socket, to_name, to_type, to_socket], ...],
          "principled": {数值输入名: float},
          "images_by_role": {"base_color":..., "normal":..., "mask":..., "emission":...},
          "normal_source": 原 DXT5nm 图名或 None,
          "normal_baked": 派生图名或 None,
          "alpha_mode": "OPAQUE"|"CLIP"|"BLEND",
          "blend_method": material.blend_method 的实际值,
        }

    - nodes / links 都**排序**（节点按 name，连线按 6 元组），读数可复算；
    - defaults 只收**数值型**输入（VALUE）；字符串/向量/着色器一律不收；
    - images_by_role 是按**实际连线**反查的（一个材质里好几张 TexImage，只给类型分不清谁是谁）。
    """
    tree = mat.node_tree
    principled = _principled_of(mat)
    by_role = _images_by_role(tree, principled)
    links = [
        [
            link.from_node.name,
            link.from_node.bl_idname,
            link.from_socket.name,
            link.to_node.name,
            link.to_node.bl_idname,
            link.to_socket.name,
        ]
        for link in tree.links
    ]
    return {
        "nodes": [
            {
                "name": node.name,
                "type": node.bl_idname,
                "image": _image_name_of(node),
                "defaults": {
                    socket.name: round(float(socket.default_value), 6)
                    for socket in node.inputs
                    if socket.type == "VALUE"
                },
            }
            for node in sorted(tree.nodes, key=lambda item: item.name)
        ],
        "links": sorted(links),
        "principled": _principled_defaults(mat, principled),
        "images_by_role": dict(by_role),
        "normal_source": str(mat.get(_PROP_NORMAL_SOURCE) or "") or None,
        "normal_baked": by_role[kernel.ROLE_NORMAL],
        "alpha_mode": str(mat.get(_PROP_ALPHA_MODE) or _alpha_mode_of(mat.blend_method)),
        "blend_method": str(mat.blend_method),
    }


def read_material_params(mat: bpy.types.Material) -> dict[str, float]:
    """Principled 读回 -> {**游戏参数名**: 值}（W5 写回用）。

    映射**只有一份**：asset_kernel.materials.PARAM_TO_NODE_INPUT（含
    _Smoothness = 1 − Roughness 那条反向）。_NormalScale 走 Normal Map 节点的
    Strength（不是 Principled 的输入），没有那个节点就跳过（不编一个数出来）。

    ⚠ **白名单**：只返回本材质**真实存在**的游戏参数（build_material_from_plan 把
    plan.scalars 的参数名记在材质属性 bamod_scalar_names 上）。不这么做的话，像 UnitV3
    这种没有 _AlphaCutoff / _EmissiveIntensity 的材质会读到 Blender 默认值（1.0 / 0.0），
    W5 就会把这两个参数**凭空写进游戏材质**。
    回退：材质上**没有** bamod_scalar_names（手搓材质 / 旧材质）⇒ 退回"全读回"。
    """
    tree = mat.node_tree
    principled = _principled_of(mat)
    normal_map = next((n for n in tree.nodes if n.type == "NORMAL_MAP"), None)
    recorded = _recorded_scalar_names(mat)
    out: dict[str, float] = {}
    for param, (logical, invert) in kernel.PARAM_TO_NODE_INPUT.items():
        if recorded is not None and param not in recorded:
            continue
        if logical.startswith("Normal Map."):
            if normal_map is None:
                continue
            socket = normal_map.inputs.get("Strength")
        else:
            socket = principled.inputs.get(principled_input_name(_principled_logical(logical)))
        if socket is None:  # pragma: no cover -- principled_input_name 已保证存在
            continue
        # ★ socket 上是**给渲染用**的值；shader 不读的参数另存了游戏原值，写回必须用它，
        #   否则会把哑光值 0.7 当成游戏的 _Smoothness=1.0 写回去 —— 那是静默改数据。
        saved = _saved_scalar(mat, param)
        # ⚠ value 是**socket 空间**的（下面还要按 invert 反一次），所以原值要先映射进 socket 空间。
        # SIM108：一行三元式（行为等价；⛔ 不用 exclude 掩盖 —— AGENTS §5）
        value = (
            float(socket.default_value) if saved is None else ((1.0 - saved) if invert else saved)
        )
        out[param] = (1.0 - value) if invert else value
    # 外观护栏把 Alpha 压成 1.0 时，_AlphaCutoff 的游戏原值记在材质属性上 —— 优先读它，
    # 否则 W5 会把 1.0 写回游戏材质（静默改数据）。⛔ 仍受上面的白名单约束。
    preserved = mat.get(_PROP_ALPHA_CUTOFF)
    if preserved is not None and _PARAM_ALPHA_CUTOFF in out:
        out[_PARAM_ALPHA_CUTOFF] = float(preserved)
    return out


# ── ★ v0.17.0 · ④ 改 · 外观 的候选来源（两个纯读函数；⛔ 面板与算子都只调这里）─────────


def material_candidate_rows() -> list[tuple[str, str]]:
    """-> [(材质名, 显示名)]：当前 .blend 里**由本插件导入过**的材质。**纯读，不读盘**。

    判据 = 材质身上有 `bamod_scalar_names`（`build_material_from_plan` 写的）。
    ⛔ 不用「名字像游戏材质」这类猜测 —— 那会把用户自己手搓的材质一起列进来，
    而写回是按**游戏里的 m_Name** 找目标的，列错了等于给一个打不中的靶子。
    顺序 = Blender 自己的顺序（= 导入顺序），每次看都一样。
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for mat in bpy.data.materials:
        if mat.get(_PROP_SCALAR_NAMES) is None:
            continue
        # ⛔ 键必须是**游戏里的材质名**（写回按它去找目标），不是 Blender 里那个带前缀的。
        game = game_name_of(mat) or mat.name
        if game in seen:
            continue
        seen.add(game)
        shader = material_shader(mat)
        out.append((game, f"{game}（{shader}）" if shader else game))
    return out


def game_name_of(mat: bpy.types.Material) -> str:
    """这个 Blender 材质对应的**游戏材质名**（没记过就空串 —— ⛔ 不拿 Blender 名顶替）。"""
    return str(mat.get(_PROP_GAME_NAME) or "")


def material_by_game_name(game_name: str) -> bpy.types.Material | None:
    """游戏材质名 -> 当前 .blend 里那个 Blender 材质（导入过才有；⛔ 找不到就 None）。"""
    for mat in bpy.data.materials:
        if game_name_of(mat) == game_name:
            return mat
    return None


def material_shader(mat: bpy.types.Material) -> str:
    """这个材质的 shader 名（没有记过就空串 —— ⛔ 不猜成 HDRP/Lit）。"""
    return str(mat.get(_PROP_SHADER) or "")


def param_rows_for(material_name: str) -> list[tuple[str, str, float, bool]]:
    """-> [(参数名, 显示名, 当前值, 驱不驱动渲染)]；**实读那个材质**。

    ⛔ 三件不许做的事：
      · 不许猜参数 —— 白名单在 `read_material_params` 里（只返回该材质真的有的）；
      · 不许回退到 Blender 默认值 —— 那正是 W5 会把 1.0 凭空写进游戏的那条路；
      · 不许因为一个材质读不动就抛 —— 材质不在 / 读挂了都返回空列表，
        由调用方（只读算子）**明说**下一步（⛔ 不给空下拉）。

    第 4 个返回值来自 `_NON_RENDERING_SCALARS`（按 shader 分组的那条实测判决）：
    False = 这个参数 shader 不读，**改了游戏画面不会变**（但写回照旧要写对，⛔ 不是没用）。
    """
    return list(param_probe(material_name)["rows"])  # type: ignore[arg-type]


def param_probe(material_name: str) -> dict[str, object]:
    """-> {"found": bool, "shader": str, "rows": [...], "error": str}。

    ★ 为什么要分三种情况：**「没找到材质」「找到了但没参数」「读挂了」是三件事**，
    面板必须说对是哪一件 —— 第一版把它们合成一句「不在当前 .blend 里」，
    真机链路探针当场抓到它指错了方向（`RU_T72B__LODs` 明明在场景里，只是它一个可写参数都没有）。
    """
    mat = material_by_game_name(material_name)
    if mat is None:
        return {"found": False, "shader": "", "rows": [], "error": "", "unreadable": []}
    shader = material_shader(mat)
    try:
        params = read_material_params(mat)
    except Exception as exc:  # noqa: BLE001 -- 面板不许因为一个材质读不动就崩
        return {
            "found": True,
            "shader": shader,
            "rows": [],
            "error": f"{type(exc).__name__}: {exc}",
            "unreadable": [],
        }
    skip = _NON_RENDERING_SCALARS.get(shader, frozenset())
    rows = [
        (param, f"{param} = {value:g}", float(value), param not in skip)
        for param, value in params.items()
    ]
    # ★ 白名单里有、却读不出当前值的那几个 —— **必须报出来**，⛔ 不许悄悄少一行。
    #   已知且正当的一族：Normal Map.Strength（_NormalScale / _NormalStrange / _Normal_Str）——
    #   read_material_params 的口径是「没有 Normal Map 节点就跳过，不编一个数出来」，
    #   而 UnitV3 的法线是 **VT 层**（静态资产里取不到）⇒ 它天生没有那个节点。
    #   ⚠ 不在这一族的缺口 = **真 bug**（真机用例钉着：
    #   _work/gui_review/probe_real_chain.py 要求「白名单 − 已列 − 读不到」= 空集）。
    unreadable = sorted(
        param
        for param in (_recorded_scalar_names(mat) or frozenset())
        if param not in params
        and str(kernel.PARAM_TO_NODE_INPUT.get(param, ("", False))[0]).startswith("Normal Map.")
    )
    return {
        "found": True,
        "shader": shader,
        "rows": rows,
        "error": "",
        "unreadable": unreadable,
    }


# --------------------------------------------------------------------------- #
# ★ 2026-10-15 · 把磁盘上一张 PNG（或内置占位图）接进 Normal / Mask
# --------------------------------------------------------------------------- #
#: 我们自己建出来的节点上的标签（值 = 角色）—— 重跑时**只清自己那几颗**。
#: ⛔ 不清整棵节点树：那会把「贴图还原」刚接好的 Base Color 一起抹掉（用户看到的是「一按就变白」）。
_MAP_TAG = "bamod_user_map"

#: 材质上记的「这张图从哪来」（磁盘路径 / 占位标记）；读回走 user_map_sources()。
_PROP_USER_MAP: Mapping[str, str] = {
    kernel.ROLE_NORMAL: "bamod_user_normal_png",
    kernel.ROLE_MASK: "bamod_user_mask_png",
}

#: 内置占位图的名字前缀 —— 读数里一眼看出「这不是你的图」。
PLACEHOLDER_PREFIX = "BAMOD占位"

#: ★ task-8 · 这张图**从哪来**的三档人话（面板与导入报告共用这一份，⛔ 不许含糊）。
#: 「打包」= Blender 把那几个字节**嵌进 .blend** 了（`image.packed_file is not None`）——
#: 用户要的就是它：「把模型中的所有东西都集成进一个 .blend 文件」。
SOURCE_DISK = "从磁盘读的"
SOURCE_PACKED = "从 .blend 里打包的图"
SOURCE_PLACEHOLDER = "内置占位图"
#: 材质属性里记「这张图是打包在 .blend 里的」那个前缀（与 PLACEHOLDER_PREFIX 同形）。
PACKED_SOURCE_PREFIX = "BAMOD打包"

#: 占位图的像素（RGBA，上到下，4x4 全同色）：
#: * 法线 = (128,128,255,255) = 平坦切空间法线（接上去**不改观感**，只证明链路是通的）；
#: * Mask = (0,255,0,128) = 非金属 · AO 满 · 光滑度 0.5（接进去反向成粗糙度 0.5）。
_PLACEHOLDER_RGBA: Mapping[str, tuple[int, int, int, int]] = {
    kernel.ROLE_NORMAL: (128, 128, 255, 255),
    kernel.ROLE_MASK: (0, 255, 0, 128),
}
#: 占位图的边长（全同色，越小越省）。
PLACEHOLDER_SIZE = 4

#: 这两路的颜色空间：**法是数据不是颜色** ⇒ 一律 Non-Color（= 0，见 kernel.colorspace）。
_USER_MAP_COLORSPACE = 0

#: 面板与读数里必须一起写出来的诚实边界（⛔ 不是游戏观感）。
USER_MAP_APPROX_NOTE = (
    "近似（⛔ 不是游戏观感）：这里只把图接进 Blender 的 Principled；游戏 shader 与 Granite VT "
    "在 Blender 没有对应物。**已经解码好的 PNG 按标准 RGB 切空间读**（B 就是 z），"
    "⛔ 不再过一次 DXT5nm —— 那只服务包里的**原始** DXT5nm 图（见 bake_normal_image）。"
    "Mask 的 AO 落点是 Mix(MULTIPLY) 乘进 Base Color（Principled 没有 AO 输入）。"
    "遮蔽 / 倒影 / 条纹三类只有用户能判（AGENTS §0.12.1）。"
)


def _tag_node(node: bpy.types.Node, tag: str) -> bpy.types.Node:
    """给**我们自己建的**节点打标签（重跑时按它认人，⛔ 不按名字猜）。"""
    node[_MAP_TAG] = tag
    return node


def _drop_own_nodes(tree: bpy.types.NodeTree, role: str) -> list[str]:
    """删掉上一次自己为这一路建的那几颗节点（返回删掉的节点名 —— 读数里要写出来）。"""
    doomed = [node for node in tree.nodes if str(node.get(_MAP_TAG) or "") == role]
    names = [node.name for node in doomed]
    for node in doomed:
        tree.nodes.remove(node)
    return names


def _mask_links() -> tuple[kernel.ChannelLink, ...]:
    """MaskMap 要接哪几条 —— 口径 = `kernel.NODE_SHAPE`（⛔ 这里不另写一份）。"""
    return tuple(link for link in kernel.NODE_SHAPE if link.role == kernel.ROLE_MASK and link.wired)


def _mask_ao_link() -> kernel.ChannelLink | None:
    """MaskMap 那条 **AO**（G）—— 口径同样照 `kernel.NODE_SHAPE`（它标着 ignored=True）。

    ⛔ `ignored=True` 是内核的**通道口径**（Principled 没有 AO 输入），不是「这条没有用」：
    用户口径是「其它与材质相关的东西，我也要在 Blender 里面看到」⇒ 由本模块给它在 Blender 里
    找一个真落点（`Mix(MULTIPLY)` 乘进 Base Color，见 `_wire_ao_into_base`）——
    **内核那条口径一个字没改**，⛔ 也没在别处另写一份通道表。
    """
    return next(
        (link for link in kernel.NODE_SHAPE if link.role == kernel.ROLE_MASK and link.ignored),
        None,
    )


#: 通道 -> 读数里的人话名（**显示用**；通道口径仍然只有 kernel.NODE_SHAPE 那一份）。
MASK_INPUT_LABEL: Mapping[str, str] = {
    "Metallic": "金属度",
    "Roughness": "粗糙度",
    "AO": "AO",
}


def _channel_origin_text(channel: str | None) -> str:
    """这个通道读数从哪颗节点取 —— 实测 5.2：A 走 TexImage 自己的 Alpha，R/G/B 走 SeparateColor。"""
    if channel == "A":
        return "TexImage.Alpha"
    return f"SeparateColor.{_SEPARATE_OUT.get(str(channel), 'Color')}"


def _mask_channel_rows() -> tuple[tuple[str, str, str], ...]:
    """MaskMap 逐路的人话：`(显示名, 来源通道, 落点)` —— 读数与面板读数都按它点名。

    来源串形如 `Mask.R`（用户要的读数形状）；落点是**实测的接线**（A 不经 SeparateColor）。
    """
    rows: list[tuple[str, str, str]] = []
    for link in _mask_links():
        label = MASK_INPUT_LABEL.get(link.blender_input, link.blender_input)
        origin = _channel_origin_text(link.channel)
        land = f"{origin} -> Principled.{link.blender_input}"
        if link.invert:
            land = f"{origin} -> Invert -> Principled.{link.blender_input}"
        rows.append((label, f"Mask.{link.channel}", land))
    ao = _mask_ao_link()
    if ao is not None:
        rows.append(
            (
                MASK_INPUT_LABEL.get(ao.blender_input, ao.blender_input),
                f"Mask.{ao.channel}",
                f"{_channel_origin_text(ao.channel)} -> Mix(MULTIPLY) -> Principled.Base Color",
            )
        )
    return tuple(rows)


def _mix_color_sockets(node: bpy.types.Node) -> tuple[bpy.types.NodeSocket, ...]:
    """ShaderNodeMix 的 (A, B, Result) 三颗 **RGBA** socket。

    ⛔ 不能按 `inputs[6]` 取：Mix 节点把各数据类型的 socket 全带在身上，索引会随版本漂；
    按 `name ＋ type` 筛才稳（实测 5.2：名字叫 A/B/Result 的各有 4 组，只有一组是 RGBA）。
    """
    left = next(s for s in node.inputs if s.name == "A" and s.type == "RGBA")
    right = next(s for s in node.inputs if s.name == "B" and s.type == "RGBA")
    result = next(s for s in node.outputs if s.name == "Result" and s.type == "RGBA")
    return left, right, result


def _wire_ao_into_base(
    principled: bpy.types.Node,
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    ao_source: bpy.types.NodeSocket,
) -> str:
    """AO -> `Mix(MULTIPLY)` -> Principled.Base Color；返回**落点**的人话（读数里要写出来）。

    为什么落在这里：Principled 在 5.2 **没有 AO 输入**（内核 NODE_SHAPE 因此把它标成 ignored）。
    乘进 Base Color 是 Blender 里 AO 贴图最常见的等价物，也是「在 Blender 里看得见」的那个位置；
    Base Color 原本没接线时，把它的默认值搬进一颗 RGB 节点当被乘数（⛔ 不拿 AO 冒充底色）。
    """
    base = principled.inputs[principled_input_name("Base Color")]
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.location = (-350.0, 250.0)
    _tag_node(mix, kernel.ROLE_MASK)
    factor = next(s for s in mix.inputs if s.name == "Factor" and s.type == "VALUE")
    factor.default_value = 1.0  # 1 = 全乘（不掺回原色）
    left, right, result = _mix_color_sockets(mix)
    if base.is_linked:
        links.new(base.links[0].from_socket, right)  # ⚠ 先留一份，再让 result 顶掉原来那条
    else:
        rgb = nodes.new("ShaderNodeRGB")
        rgb.location = (-600.0, 300.0)
        rgb.outputs[0].default_value = tuple(float(value) for value in base.default_value)
        _tag_node(rgb, kernel.ROLE_MASK)
        links.new(rgb.outputs[0], right)
    links.new(ao_source, left)
    links.new(result, base)
    return "SeparateColor.Green -> Mix(MULTIPLY) -> Principled.Base Color"


def _unwrap_ao_multiply(material: bpy.types.Material, principled: bpy.types.Node) -> bool:
    """把上一次自己插的 AO 乘法拆掉（Base Color 接回它原来那张图）；返回是否拆过。

    ⛔ 为什么必须先拆再删：AO 是**乘进 Base Color** 的 —— 直接删掉那颗 Mix 会让 Base Color
    变成一个没人接的输入（用户看到的是「再点一次，底色没了」）。拆的时候只认**自己打的标签**，
    别人的连线一根不碰。
    """
    tree = material.node_tree
    base = principled.inputs[principled_input_name("Base Color")]
    for link in list(base.links):
        node = link.from_node
        if node.bl_idname != "ShaderNodeMix":
            continue
        if str(node.get(_MAP_TAG) or "") != kernel.ROLE_MASK:
            continue
        left, right, _result = _mix_color_sockets(node)
        for socket in (left, right):
            for inner in socket.links:
                if str(inner.from_node.get(_MAP_TAG) or ""):
                    continue  # 我们自己那一支（AO / 占位底色）—— 丢掉
                tree.links.new(inner.from_socket, base)
                return True
        return False
    return False


def _wire_normal_image(
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    principled: bpy.types.Node,
    image_name: str,
    strength: float | None,
    *,
    tag: str | None = None,
    location: tuple[float, float] = (-600.0, -200.0),
) -> bpy.types.Node:
    """一张**已经是 RGB 切空间**的法线图 -> NormalMap(Strength) -> Principled.Normal。

    ★ 接线形状与强度**只有这一份**：包里读出来的派生图（`_wire_normal`）与用户自己画的 PNG
    （`bind_user_map`）都走它。履带那颗材质实测的读数就是它：NORMAL_MAP 强度 4.0 <-
    RU_Track7290_Standard_22_Normal__NormalRGB（512² Non-Color）。
    ⛔ 用户画的 PNG 已经是 RGB 切空间 ⇒ **不再过一次 DXT5nm**（那只服务包里的原图）。

    :param strength: None = 不动 Blender 的默认（1.0）。
    """
    tex = _image_node(nodes, image_name, location)
    normal_map = nodes.new("ShaderNodeNormalMap")
    normal_map.location = (location[0] + 300.0, location[1])
    if tag:
        _tag_node(tex, tag)
        _tag_node(normal_map, tag)
    if strength is not None:
        normal_map.inputs["Strength"].default_value = float(strength)
    links.new(tex.outputs["Color"], normal_map.inputs["Color"])
    links.new(normal_map.outputs["Normal"], principled.inputs[principled_input_name("Normal")])
    return normal_map


def _check_strength(strength: float | None) -> None:
    """NORMAL_MAP 的强度必须是正的有限数。

    ⛔ 0 / 负数 / NaN 一律点名，不静默接一张没效果的法线。
    """
    if strength is None:
        return
    value = float(strength)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"NORMAL_MAP 强度必须是正的有限数，给的是 {strength!r}")


# --------------------------------------------------------------------------- #
# ★ task-8 · 图从哪来：**打包进 .blend 的图**也能用（复用 / 回落 / 点名来源）
# --------------------------------------------------------------------------- #
# 用户目标（原话）：「我的源的话，.blend 文件里可以包含浮雕、金属度、光滑度的文件吗？
# 也就是说把模型中的所有东西，都集成进一个 .blend 文件」。
# 今天之前这里只吃磁盘路径（`if not path.is_file(): raise`）⇒ 图被 Blender 打包
# （`image.packed_file is not None`）、盘上原文件搬走 / 删掉之后，这一路就用不了了。
#
# 三条口径（缺一条就退回旧毛病）：
#   1. **先**按文件名在 `bpy.data.images` 里找**已打包**的那张（`packed_file is not None`
#      且尺寸非 0）⇒ 复用；找不到再回落磁盘路径；
#   2. 两边都没有 ⇒ **保持点名报错**（⛔ 不许静默、⛔ 不许拿占位图冒充）；
#   3. 读数**点名来源**（`SOURCE_DISK` / `SOURCE_PACKED`）—— 面板与导入报告都读它。


def _packed_usable(image) -> bool:
    """这张图能不能当「已经打包好的那张」用 —— **真打包过** 且尺寸非 0。

    ⛔ 「`bpy.data.images` 里有同名图」**不够**：那张可能只是从磁盘 load 进来的
    （`packed_file is None`，盘上文件一搬就废），也可能是一颗 0 尺寸的空壳。
    """
    if image is None or getattr(image, "packed_file", None) is None:
        return False
    return tuple(int(value) for value in image.size) != (0, 0)


def _packed_images() -> list:
    """`bpy.data.images` 里**真打包过**的那些（拿不到图库 ⇒ 空表，⛔ 不炸）。

    ⚠ 为什么要兜住「拿不到图库」：`materials` 里那几个**纯函数**（路径约定 / 门控）
    要能在**没有 bpy 实例**的环境里被量（`tests/test_vt_auto_bind.py` 用最小桩），
    ⛔ 不许因为「查了一下打包图」就抛 AttributeError。
    """
    images = getattr(getattr(bpy, "data", None), "images", None)
    if images is None:
        return []
    return [image for image in images if _packed_usable(image)]


def packed_image_by_name(name: str):
    """按**文件名**找那张已打包的图（`name` 带不带 `.png` 都认；`xx.png.001` 也认）。

    ⛔ 不猜：同名有多张（Blender 给重名加的 `.001` 后缀）时**只认精确同名那张**；
    精确同名没有、而带 `.` 后缀那张**唯一**才用它；否则返回 None（由调用方点名）。
    """
    wanted = Path(str(name or "")).name
    if not wanted:
        return None
    stem = Path(wanted).stem
    packed = _packed_images()
    for candidate in (wanted, stem):
        hit = next((image for image in packed if image.name == candidate), None)
        if hit is not None:
            return hit
    dotted = [image for image in packed if image.name.startswith(stem + ".")]
    return dotted[0] if len(dotted) == 1 else None


def packed_map_image(role: str, unit_key: str = "") -> tuple:
    """`(image, note)` —— 场景里有没有**同名打包图**（`<单位键><后缀>`，见 `VT_MAP_SUFFIX`）。

    挑法与磁盘那一份（`game_map_files`）逐条同构：先按**单位键**对上文件名，
    **对不上就一张都不接**；只有 `unit_key` 为空时才退到「唯一候选」，
    多份又对不上就明写「分不清」（⛔ 不替你猜一个）。
    """
    suffix = str(VT_MAP_SUFFIX.get(role) or "")
    if not suffix:
        return None, f"不认识的图角色：{role!r}"
    stem = suffix[:-4] if suffix.endswith(".png") else suffix
    keyed = None
    if unit_key:
        for wanted in (f"{unit_key}{suffix}", f"{unit_key}{stem}"):
            keyed = packed_image_by_name(wanted)
            if keyed is not None:
                break
    if keyed is not None:
        return keyed, ""
    candidates = [
        image
        for image in _packed_images()
        if image.name.endswith(suffix) or image.name.endswith(stem)
    ]
    if len(candidates) == 1 and not unit_key:
        return candidates[0], f"{role}：按唯一候选取的**打包**图 {candidates[0].name}"
    if len(candidates) == 1:
        # ⛔ 同 game_map_files：单位键对不上 ⇒ 不接（别把别的单位的坐标图配上）
        return None, (
            f"对不上单位 {role}：场景里只有打包图 {candidates[0].name}，与 {unit_key} 无关 ⇒ 不接"
        )
    if len(candidates) > 1:
        shown = "、".join(image.name for image in candidates[:4])
        return None, f"分不清 {role}：场景里有 {len(candidates)} 张打包图候选（{shown}）"
    return None, ""


def _set_user_colorspace(image) -> None:
    """这两路的颜色空间照旧 Non-Color —— 磁盘 / 打包**两条来源共用这一份**。"""
    apply_colorspace(image, _USER_MAP_COLORSPACE)  # ⚠ 只改标签，不动像素
    expected = colorspace_of(_USER_MAP_COLORSPACE)
    if image.colorspace_settings.name != expected:  # pragma: no cover -- Blender 不会拒绝
        raise ValueError(
            f"图 {image.name} 的颜色空间没设成 {expected}"
            f"（现在是 {image.colorspace_settings.name}）—— 法线会被当颜色解一次，读数就不对了"
        )


def _load_user_image(role: str, png: str) -> tuple[bpy.types.Image, bool, bool, str]:
    """这张图 -> `bpy.data.images` 里那颗（颜色空间照旧 Non-Color）。

    ★ task-8：`png` 可以是**磁盘路径**，也可以是**图名**（那张图打包在 .blend 里）。
    查找顺序（用户要「把模型里所有东西集成进一个 .blend」）：

    1. `png` 空 ⇒ 内置占位图（原样，读数「内置占位图」）；
    2. 按**文件名**找**已打包**的那张（`packed_file is not None` 且尺寸非 0）⇒ 复用，
       读数「从 .blend 里打包的图」；
    3. 回落磁盘：`Path(png).is_file()` ⇒ 照旧 `bpy.data.images.load(check_existing=True)`，
       读数「从磁盘读的」；
    4. 两边都没有 ⇒ `ValueError` **把那两处都点名**（⛔ 不静默、⛔ 不拿占位图冒充）。

    :returns: (图, 是不是占位图, 是不是复用的, **来源**（SOURCE_* 之一）)
    :raises ValueError: 两处都没有 / 磁盘上那张读不成图 / 尺寸为 0。
    """
    if not png:
        red, green, blue, alpha = _PLACEHOLDER_RGBA[role]
        side = PLACEHOLDER_SIZE
        suffix = "Normal" if role == kernel.ROLE_NORMAL else "Mask"
        image, reused = _ensure_image(
            f"{PLACEHOLDER_PREFIX}_{suffix}",
            side,
            side,
            bytes((red, green, blue, alpha)) * (side * side),
            _USER_MAP_COLORSPACE,
            reuse=True,
        )
        return image, True, reused, SOURCE_PLACEHOLDER
    packed = packed_image_by_name(png)
    if packed is not None:
        _set_user_colorspace(packed)
        # 「复用」= 这颗 datablock 本来就在场景里（打包进来的），不是这次新建的
        return packed, False, True, SOURCE_PACKED
    path = Path(png)
    if not path.is_file():
        looked = path.name if path.stem == path.name else f"{path.name} / {path.stem}"
        raise ValueError(
            f"找不到这张图：{path}（磁盘上没有这个文件；bpy.data.images 里也没有同名的"
            f"**打包**图（找过 {looked}）—— 要么给对路径，要么把它打包进 .blend）"
        )
    before = bpy.data.images.get(path.name)
    try:
        image = bpy.data.images.load(str(path), check_existing=True)
    except Exception as exc:  # 读不出来就点名，⛔ 不静默跳过
        raise ValueError(f"读不出一张图：{path}（{type(exc).__name__}: {exc}）") from exc
    if image is None or tuple(int(value) for value in image.size) == (0, 0):
        if image is not None:
            bpy.data.images.remove(image)  # ⛔ 不留一颗空壳图在 bpy.data.images 里
        raise ValueError(f"这张图没有像素（不是一张能读的图？）：{path}")
    _set_user_colorspace(image)
    return image, False, bool(before is not None and before.name == image.name), SOURCE_DISK


def _principled_or_new(material: bpy.types.Material) -> bpy.types.Node:
    """拿这颗材质的 Principled；**没有就建一颗**，并把 Material Output 接上。

    为什么要建（而不是报错）：这一格是「把图接进材质」—— 用户自己搓的材质 / 自己导入的模型
    也该接得进去，这正是「未来给自己的模型用法线」那条路。
    """
    material.use_nodes = True
    tree = material.node_tree
    node = next((item for item in tree.nodes if item.type == "BSDF_PRINCIPLED"), None)
    if node is not None:
        return node
    node = tree.nodes.new("ShaderNodeBsdfPrincipled")
    node.location = (0.0, 0.0)
    output = next((item for item in tree.nodes if item.type == "OUTPUT_MATERIAL"), None)
    if output is None:
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        output.location = (300.0, 0.0)
    if not output.inputs["Surface"].is_linked:
        tree.links.new(node.outputs["BSDF"], output.inputs["Surface"])
    return node


def _map_extra_note(role: str, image: bpy.types.Image) -> str:
    """这条路上**必须一起说**的一件事实（现在只有一条：Mask 图没有 A 通道）。"""
    if role == kernel.ROLE_MASK and int(getattr(image, "depth", 24)) == 24:
        return (
            "这张 Mask 图没有 A 通道（位深 24）⇒ A 读成 1.0、反向之后**粗糙度 = 0（镜面）**；"
            "要 A 通道就存成 RGBA PNG"
        )
    return ""


@dataclass(frozen=True, slots=True)
class BindReadout:
    """一路「自己那张图」接上去以后的读数（图 / 尺寸 / 颜色空间 / 接线形状 / 来源）。"""

    role: str
    image: str
    source: str
    placeholder: bool
    width: int
    height: int
    colorspace: str
    strength: float | None = None
    chain: str = ""
    reused: bool = False
    note: str = ""
    #: 逐路点名：`(显示名, 来源通道, 落点)`。
    #: 例：`("金属度", "Mask.R", "SeparateColor.Red -> Principled.Metallic")`。
    #: 法线那一路是空的（它只有一张图，来源就是图本身）。
    channels: tuple[tuple[str, str, str], ...] = ()
    #: ★ task-8：这一路的图**从哪来**（`SOURCE_DISK` / `SOURCE_PACKED` / 占位）——
    #: 读数必须点名，⛔ 不许含糊（用户要的是「图在 .blend 里」也能看出来）。
    source_label: str = ""

    def line(self) -> str:
        """这一路的人话：**哪张图 · 尺寸 · 颜色空间 · 逐路点名**。

        形状（用户要的读数就是这个样子）::

            法线 ← RU_T72B_normal_decoded.png（2048x2048 · Non-Color · 强度 1 · 图新建）
            MaskMap ← RU_T72B_mask_raw.png（2048x2048 · Non-Color · 图新建）｜金属度 ← Mask.R
        """
        label = _ROUTE_LABEL.get(self.role, self.role)
        origin = f"内置占位图 {self.image}" if self.placeholder else self.image
        strength = f" · 强度 {self.strength:g}" if self.strength is not None else ""
        reuse_text = "复用" if self.reused else "新建"
        pair = " · ".join(f"{name} ← {where}" for name, where, _land in self.channels)
        tail = f"｜{pair}" if pair else (f"｜{self.chain}" if self.chain else "")
        # ★ task-8：来源紧跟在那对括号后面（`｜从磁盘读的` / `｜从 .blend 里打包的图`）
        src = f"｜{self.source_label}" if self.source_label else ""
        return (
            f"{label} ← {origin}（{self.width}x{self.height} · {self.colorspace}{strength} · "
            f"图{reuse_text}）{src}{tail}"
        )

    def detail_lines(self) -> list[str]:
        """逐路的**落点**明细（面板「看详情」那一档；摘要只画上面那行）。"""
        out = [f"      {name} ← {where} ｜ {land}" for name, where, land in self.channels]
        if self.note:
            out.append(f"      ⛔ {self.note}")
        return out


@dataclass
class BindReport:
    """一次「把图接进材质」的读数（面板画摘要、探针做判据都用它）。"""

    material: str = ""
    #: 这批图**从哪来**（例：`游戏原图（Granite VT 抽出来的）`）—— 空 = 从磁盘挑的。
    origin: str = ""
    routes: list[BindReadout] = field(default_factory=list)
    #: 角色 -> **按实际连线反查**出来的图名（`_images_by_role`）；⛔ 不是「打算接的那张」。
    actual: dict[str, str | None] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def wired(self) -> dict[str, str]:
        """角色 -> 实际接上的图名。"""
        return {role: str(image) for role, image in self.actual.items() if image}

    def summary(self) -> str:
        """一句人话：哪几路接上了 · 各用了哪张图（读数要能直接看）。"""
        tail = f" · 错误 {len(self.errors)} 条" if self.errors else ""
        where = f"{self.origin} ｜ " if self.origin else ""
        if not self.routes:
            return f"{where}{self.material or '这颗材质'}：一路都没接上{tail}"
        head = "、".join(
            f"{_ROUTE_LABEL.get(item.role, item.role)}={item.image}" for item in self.routes
        )
        holder = "（含内置占位图）" if any(item.placeholder for item in self.routes) else ""
        return f"{where}{self.material} ｜ 接上 {len(self.routes)} 路：{head}{holder}{tail}"

    def lines(self) -> list[str]:
        """读数行（第 1 行 = 摘要；面板只画摘要，明细进「看详情」）。"""
        out = [self.summary()]
        for item in self.routes:
            out.append("    " + item.line())
            out.extend(item.detail_lines())
        if self.actual:
            wired = self.wired()
            out.append(
                "    · 读回（按实际连线）："
                + " · ".join(
                    f"{_ROUTE_LABEL.get(role, role)}={wired.get(role) or '（没接）'}"
                    for role in _ROUTE_ORDER
                )
            )
        out.extend("⛔ " + error for error in self.errors)
        out.append(USER_MAP_APPROX_NOTE)
        return out


def _wire_user_map(
    material: bpy.types.Material,
    role: str,
    image: bpy.types.Image,
    *,
    placeholder: bool,
    source: str,
    reused: bool,
    strength: float | None,
    packed: bool = False,
    source_label: str = "",
) -> BindReadout:
    """把一张已经在 `bpy.data.images` 里的图接进这一路（幂等：先清自己那几颗、再重接）。

    :param packed: ★ task-8：这张图**打包在 .blend 里** ⇒ 记的是标记（`PACKED_SOURCE_PREFIX`），
        ⛔ 不是那条可能已经失效的磁盘路径；读数也按它点名来源。
    """
    principled = _principled_or_new(material)
    tree = material.node_tree
    if role == kernel.ROLE_MASK:
        # ★ 必须在 _drop_own_nodes **之前**：AO 是乘进 Base Color 的，先删 Mix 会把底色一起弄丢
        _unwrap_ao_multiply(material, principled)
    _drop_own_nodes(tree, role)
    channels: tuple[tuple[str, str, str], ...] = ()
    if role == kernel.ROLE_NORMAL:
        node = _wire_normal_image(
            tree.nodes, tree.links, principled, image.name, strength, tag=role
        )
        actual = round(float(node.inputs["Strength"].default_value), 6)
        chain = f"TexImage -> NormalMap(Strength={actual:g}) -> Principled.Normal"
    else:
        _wire_mask(tree.nodes, tree.links, principled, image.name, list(_mask_links()), tag=role)
        actual = None
        chain = ""
        channels = _mask_channel_rows()
    if packed:
        record = f"{PACKED_SOURCE_PREFIX}({image.name})"
    elif source:
        record = str(source)
    else:
        record = f"{PLACEHOLDER_PREFIX}({image.name})"
    material[_PROP_USER_MAP[role]] = record
    return BindReadout(
        role=role,
        image=str(image.name),
        source=str(source),
        placeholder=bool(placeholder),
        width=int(image.size[0]),
        height=int(image.size[1]),
        colorspace=str(image.colorspace_settings.name),
        strength=actual,
        chain=chain,
        reused=bool(reused),
        note=_map_extra_note(role, image),
        channels=channels,
        source_label=str(source_label),
    )


def bind_user_map(
    material: bpy.types.Material,
    role: str,
    png: str = "",
    *,
    strength: float | None = None,
) -> BindReadout:
    """把**磁盘上一张图**（`png` 空 = 内置占位图）接进这颗材质的 Normal / Mask（单路）。

    ⛔ 内核只有一份：MaskMap 的通道对应（R->Metallic · A->Invert->Roughness · G 只标注）
    与法线的接线形状都取自 `kernel.NODE_SHAPE` / 本模块既有的 `_wire_*` —— 这里没有第二份公式。

    :raises ValueError: 角色不认识 / 强度不是正的有限数 / 这张图读不出来。
    """
    if role not in (kernel.ROLE_NORMAL, kernel.ROLE_MASK):
        raise ValueError(f"只支持把图接进 Normal / Mask；给的角色是 {role!r}")
    _check_strength(strength)
    image, placeholder, reused, source_label = _load_user_image(str(role), str(png or ""))
    return _wire_user_map(
        material,
        role,
        image,
        placeholder=placeholder,
        source=str(png or ""),
        reused=reused,
        strength=strength,
        packed=source_label == SOURCE_PACKED,
        source_label=source_label,
    )


def bind_user_maps(
    material: bpy.types.Material,
    *,
    normal_png: str = "",
    mask_png: str = "",
    normal_strength: float | None = None,
    roles: tuple[str, ...] = (kernel.ROLE_NORMAL, kernel.ROLE_MASK),
    placeholder: bool = False,
    origin: str = "",
) -> BindReport:
    """一把接两路（面板那颗「接到材质」的能力入口）。

    * **先把每一路的图都取到手，再动节点树** —— 任何一路读不出来就当场 `ValueError`，
      节点树一个字节都没动过（⛔ 不许「接了一半 ＋ 一句静默」）；
    * `roles` 点到的每一路：给了图就用图；**没给**时只有 `placeholder=True` 才用内置占位图，
      否则 `ValueError` 点名（⛔ 不许「按了没反应」）；
    * 读数 `report.lines()` 逐路写清「用的哪张图 · 什么颜色空间 · **逐路点名**（金属度 ← Mask.R ·
      粗糙度 ← Mask.A · AO ← Mask.G）」，并按**实际连线**反查一遍（`_images_by_role`），
      ⛔ 不拿「打算接的那张」冒充结果；
    * Mask 的 **AO（G）** 会在 Blender 里找一个真落点（`Mix(MULTIPLY)` 乘进 Base Color）——
      Principled 没有 AO 输入（内核 NODE_SHAPE 因此标 ignored），这条口径没改；
    * `origin` 只是**读数抬头**（例：`游戏原图（Granite VT 抽出来的）`），不影响接线。

    :raises ValueError: roles 里有不认识的角色 / 没图也没勾占位 / 强度不合法 / 图读不出来。
    """
    wanted = tuple(roles)
    unknown = [role for role in wanted if role not in (kernel.ROLE_NORMAL, kernel.ROLE_MASK)]
    if unknown:
        raise ValueError(f"只支持 Normal / Mask；roles 里有多余的 {unknown!r}")
    paths = {kernel.ROLE_NORMAL: str(normal_png or ""), kernel.ROLE_MASK: str(mask_png or "")}
    loaded: list[tuple[str, bpy.types.Image, bool, bool, str]] = []
    for role in wanted:
        png = paths[role]
        if not png and not placeholder:
            raise ValueError(
                f"{_ROUTE_LABEL.get(role, role)}：还没选图 —— 先点「选图…」，"
                "或勾上「没选就用占位图」"
            )
        _check_strength(normal_strength if role == kernel.ROLE_NORMAL else None)
        image, is_placeholder, reused, source_label = _load_user_image(str(role), png)
        loaded.append((role, image, is_placeholder, reused, source_label))
    report = BindReport(material=str(material.name), origin=str(origin))
    for role, image, is_placeholder, reused, source_label in loaded:
        report.routes.append(
            _wire_user_map(
                material,
                role,
                image,
                placeholder=is_placeholder,
                source=paths[role],
                reused=reused,
                strength=normal_strength if role == kernel.ROLE_NORMAL else None,
                packed=source_label == SOURCE_PACKED,
                source_label=source_label,
            )
        )
    report.actual = dict(_images_by_role(material.node_tree, _principled_of(material)))
    return report


def map_source_label(source: str) -> str:
    """材质上记的那条来源 -> **一句人话**（★ task-8：面板那一行也照它说，⛔ 不许含糊）。

    记的三种形态（`user_map_sources` 的返回值）：

    * 空串 ⇒ 「没接过」；
    * `PACKED_SOURCE_PREFIX(图名)` ⇒ `SOURCE_PACKED`（⛔ 别把它当磁盘路径）；
    * `PLACEHOLDER_PREFIX(图名)` ⇒ `SOURCE_PLACEHOLDER`；
    * 其余 ⇒ 磁盘上那个路径（`SOURCE_DISK` ＋ 实际路径）。
    """
    text = str(source or "")
    if not text:
        return "没接过"
    if text.startswith(PACKED_SOURCE_PREFIX):
        tail = text[len(PACKED_SOURCE_PREFIX) :].strip("()")
        return f"{SOURCE_PACKED}（{tail}）"
    if text.startswith(PLACEHOLDER_PREFIX):
        return SOURCE_PLACEHOLDER
    return f"{SOURCE_DISK}：{text}"


def user_map_sources(material: bpy.types.Material) -> dict[str, str]:
    """材质上记的「这两张图从哪来」（**磁盘路径 / 打包标记 / 占位标记**）；没接过就是空串。

    ⚠ 拿它显示人话请走 `map_source_label(...)` —— ⛔ 别自己 split，"BAMOD打包(…)混在里面。
    """
    return {role: str(material.get(prop) or "") for role, prop in _PROP_USER_MAP.items()}
