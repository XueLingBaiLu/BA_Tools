"""W4 · 材质还原：六张映射表 + 统一节点树形状 + 还原计划。

**纯数据 + 纯函数**：不依赖 Blender 宿主、不依赖 UnityPy，产品 venv 里就能跑判据
（asset_kernel 的 C5 契约：内核里连宿主模块的名字都不许出现，见 tests/test_contracts.py）。
节点树的**形状**在这里定死（NODE_SHAPE），真正建节点在 blender_addon/materials.py
（那边只做"照计划执行"）。

口径出处（设计稿 设计-Blender插件-游戏外观还原.md）：
§3.3 shader 分布与典型案例 · §3.4 Layer_<hash> 两轮证据 · §3.5 贴图与通道 ·
§3.9 运行时项的反编译依据 · §7.1 四张映射表 · §7.2 节点树 · §7.3 Layer_ 处理口径。

口径（2026-10-07 更正过理由，与 docs 的更正同一版措辞）：
1. Layer_<hash> **直接当 Base Color** —— 理由**不是**"全库 9,666 个容器条目里
   0 个 TextureStack 资产"
   （那条结论已作废），而是：UnitV3 的 **albedo 那块没有 VT 属性**，另两块才是 VT 层。
   * `Layer_A97CDC25 ("Albedo")` **无** [TextureStack.*] ⇒ 普通 2D 贴图，静态可取
     ⇒ 当 Base Color 是对的；
   * `[TextureStack._Textures(0)] Layer_8D60137F ("Normal")` /
     `(1) Layer_4C4561B0 ("Mask")` 是 **VT 层**。
   * `m_BuildTextureStacks` = Unity **虚拟纹理（VirtualTexturing）的材质级 stack 绑定**，
     **不是**冗余字段 ——
     `研究-模型与动画/UnityCsReference-2022.3.62f3/UnityCsReference/ScriptBindings/VirtualTexturing.bindings.cs:51`
     `ValidateMaterialTextureStacks(Material)` /
     `:190 GetTextureStackSize(Material, int, out int, out int)`。
   * 槽位证据：`unity-ba-rip-all/Assets/Shader/Shader Graphs_UnitV3.shader:3-5`。
2. MaskMap 的 m_ColorSpace 原样就是 1（sRGB）——**照抄 Unity 的取值**，别按常识改成 Non-Color。

⛔ **本版明确"没做"的**（是能力缺口，不是 bug；⛔ 不许假装做了）：
* **虹彩 / Thin Film**：HDRP/Lit 的 `_IridescenceThicknessMap` /
  `_IridescenceThicknessRemap` / `_IridescenceMask`
  既不在本模块的槽位表里、也不在写侧保留清单里，而 `_MATERIAL_FEATURE_IRIDESCENCE`
  关键词会被**原样带走**
  ⇒ 游戏侧退化成 shader 默认（白图 + Remap(0,1)）= **没有图案的均匀薄膜**。
  实测：11,902 个真材质里 **135** 个绑了该图
  （读数 `_work/audit-material/tools/probe_g_coverage.py`）。
* **M4 `Shader Graphs/UnitV4_Camo`**：**2026-10-11 起颜色层由 shader 自写的 desc 认**
  （`base_color_descs=("Albedo","Camo")`，槽名 hash 一个字都不写死）——
  但它仍标 `unsupported=True`（"还原计划不展开它"这条口径是 2026-10-10 定的，不属本卡；
  `kb/19` §5 的 E4 缺口销的是**槽位表**那一半）。
* **块压缩贴图的新建**：`asset_kernel/texture_build.py` 对 10/12/28/29 走「**原样搬运**」
  （2026-10-09 起）—— 能搬、不能改像素；要改像素得先解成无压缩。
"""

from __future__ import annotations

from collections.abc import Container, Iterable, Mapping
from dataclasses import dataclass, field

__all__ = [
    "NODE_SHAPE",
    "COLOR_TO_NODE_INPUT",
    "PARAM_TO_NODE_INPUT",
    "DESC_ALBEDO",
    "DESC_CAMO",
    "ROLE_BASE_COLOR",
    "ROLE_EMISSION",
    "ROLE_IRIDESCENCE",
    "ROLE_CAMO",
    "CAMO_MIX_WEIGHT",
    "ROLE_MASK",
    "ROLE_NORMAL",
    "RUNTIME_ITEMS",
    "RUNTIME_KEYS_UNITV3",
    "TABLE_M1",
    "TABLE_M2",
    "TABLE_M3",
    "TABLE_M4",
    "TABLE_M5",
    "TABLE_M6",
    "TABLES",
    "ChannelLink",
    "MaterialPlan",
    "MaterialTable",
    "RuntimeItem",
    "ScalarBinding",
    "SlotBinding",
    "UnitPlan",
    "plan_material",
    "plan_unit",
    "table_for",
    "worst_coverage",
]

# --------------------------------------------------------------------------- #
# 角色 / 表号
# --------------------------------------------------------------------------- #
ROLE_BASE_COLOR = "base_color"
ROLE_NORMAL = "normal"
ROLE_MASK = "mask"
ROLE_EMISSION = "emission"
#: ★ 2026-10-17：HDRP 虹彩厚度层（`_IridescenceThicknessMap`，R 通道）。实测 68 颗 HDRP/Lit 绑了它，
#: 全部满足 `_MATERIAL_FEATURE_IRIDESCENCE` 在册；`_IridescenceThicknessRemap` 决定 0..1 -> 实际值。
ROLE_IRIDESCENCE = "iridescence"
#: ★ 2026-10-17：`UnitV4_Camo` 的**涂装层**（shader 自写 desc = 'Camo'，槽名 `Layer_<hash>_1`）。
#: 它**不是**第二张底图：同一族里皮肤 1 的这层近乎纯色、皮肤 2/3 是真迷彩花纹，
#: 并且自带平铺/偏移（`_CamoTiling` / `_OffsetX` / `_OffsetY`，逐套皮肤都不同）。
#: ⛔ 合成式**没钉死**（见 `CAMO_MIX_WEIGHT`）；接线时按实测权重当参数，不假装知道 shader 的算法。
ROLE_CAMO = "camo"

#: shader **自己写的**那几个 desc（`m_ParsedForm.m_PropInfo.m_Props[].m_Description`）——
#: 认颜色层**只认这些字**，⛔ 不认槽名里的 hash。口径与 `texture_install.COLOR_LAYER_DESCS` 同一份。
DESC_ALBEDO = "Albedo"
DESC_CAMO = "Camo"

#: 涂装层（`Camo`）在 Blender 侧的**混合权重**（`Mix` 节点 factor），⛔ 不是 shader 的公式。
#: 实测依据：`_work/camo/Unity探针-结论-第二轮.md` §2 —— Albedo 固定成红、把 Camo 从红换绿，
#: 读数 `(66,40,40) -> (63,46,41)`，量程 25 级 ⇒ 涂装层参与约 **24%**；
#: 同一轮还量到：**不是相乘**（交换两槽颜色读数不同）、**不是整层覆盖**（主色跟 Albedo 走）。
#: ⚠ 那轮的 diffuse 正控没通（全白 vs 全黑只差 1 级）⇒ 24% 只在镜面路径上量到；
#: 2026-10-17 的 `CamoCompositeProbe` 想在均匀白环境下重测，**正控仍红**（albedo 黑/白只差 0.004），
#: ⇒ **精确合成式仍未知**。默认值取实测的 0.24，做成材质属性 `bamod_camo_weight` 可调。
CAMO_MIX_WEIGHT = 0.24

TABLE_M1 = "M1"
TABLE_M2 = "M2"
TABLE_M3 = "M3"
TABLE_M4 = "M4"
TABLE_M5 = "M5"
TABLE_M6 = "M6"
TABLE_NONE = "—"

#: 还原度（面板上的那一格）。四档口径写死，别在各处自己拼字符串。
COVERAGE_FULL = "完整"
COVERAGE_PARTIAL = "部分"
COVERAGE_UNSUPPORTED = "未支持"
COVERAGE_UNKNOWN = "未识别"


# --------------------------------------------------------------------------- #
# 统一节点树形状（§7.2）—— **表无关**，六张表共用这一个形状
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ChannelLink:
    """一条"从某张图到某个 Principled 输入"的连线（形状，不是实例）。"""

    role: str
    blender_input: str
    #: "R"/"G"/"B"/"A" = 先过一个 Separate Color 取该通道；None = 整张颜色直接接。
    channel: str | None = None
    #: True = 中间插一个 Invert（Roughness = 1 − Smoothness）。
    invert: bool = False
    #: True = **标注忽略**：Principled 没有这个输入（AO），只在报告/面板里显形，不接线。
    ignored: bool = False
    note: str = ""

    @property
    def wired(self) -> bool:
        return not self.ignored


#: §7.2 的节点树，逐条列在这里（E1/E2 就断言它）。
NODE_SHAPE: tuple[ChannelLink, ...] = (
    ChannelLink(ROLE_BASE_COLOR, "Base Color"),
    ChannelLink(ROLE_NORMAL, "Normal", note="DXT5nm：先重建 Z，再进 Normal Map"),
    ChannelLink(ROLE_MASK, "Metallic", channel="R"),
    ChannelLink(ROLE_MASK, "Roughness", channel="A", invert=True),
    ChannelLink(
        ROLE_MASK,
        "AO",
        channel="G",
        ignored=True,
        note="Principled 没有 AO 输入 ⇒ 标注忽略（§7.2 的「或标注忽略」）",
    ),
    ChannelLink(ROLE_EMISSION, "Emission Color"),
    #: 虹彩厚度：纹理 R（0..1 微米）**先过 `_IridescenceThicknessRemap`，再 ×1000 变纳米** ——
    #: 那两步是 Math 节点，插件侧按 `_apply_iridescence` 补（不在 NODE_SHAPE 里表达）。
    ChannelLink(ROLE_IRIDESCENCE, "Thin Film Thickness", channel="R",
                note="HDRP 单位是微米（BSDF.hlsl:483 “Mean 0.5 is 500nm”）⇒ 进节点前 ×1000"),
)


# --------------------------------------------------------------------------- #
# 运行时项（§7.3 / D3 的「不还原清单」—— 5 条，一条不许少）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class RuntimeItem:
    """一条**运行时**才存在、静态资产里没有对应东西的项。"""

    key: str
    label: str
    evidence: str

    def line(self) -> str:
        return f"{self.key} —— {self.label}（{self.evidence}）"


_RUNTIME_DIRT = RuntimeItem(
    key="_DIRT",
    label="脏污 keyword：运行期按战况 Material.EnableKeyword/DisableKeyword 开关",
    evidence='ProceduralDirt.txt:324 IsKeywordEnabled("_DIRT") / :387 EnableKeyword',
)
_RUNTIME_DIRT_PROGRESS = RuntimeItem(
    key="_DirtProgress",
    label="脏污进度：运行期 SetFloat，随战况增长",
    evidence='ProceduralDirt.txt:1141 PropertyToID("_DirtProgress") / :793 间接 SetFloat',
)
_RUNTIME_POTATO = RuntimeItem(
    key="_POTATO_MOD",
    label="低配开关：全库只有 1 个材质 =1（VDV），运行期决定要不要降配",
    evidence="survey_materials.py：275 个材质带该浮点，=1 的 1 个",
)
_RUNTIME_STRANGE = RuntimeItem(
    key="_AOStrange/_NormalStrange",
    label="AO / 法线强度：材质浮点（实测 3.0 / 1.0），按画质档位在运行期可能被改",
    evidence="材质浮点实测 + 设计稿 §3.9（运行期改材质只有 2+1 处）",
)
_RUNTIME_TRACK_SCROLL = RuntimeItem(
    key="track_uv_scroll",
    label="履带 UV 滚动：每帧 Material.set_mainTextureOffset 让履带贴图动",
    evidence="Tracks.txt:691/707/733/759 Material.set_mainTextureOffset",
)

#: key -> RuntimeItem（面板与用例都按 key 找，不靠散文匹配）。
RUNTIME_ITEMS: Mapping[str, RuntimeItem] = {
    item.key: item
    for item in (
        _RUNTIME_DIRT,
        _RUNTIME_DIRT_PROGRESS,
        _RUNTIME_POTATO,
        _RUNTIME_STRANGE,
        _RUNTIME_TRACK_SCROLL,
    )
}

#: ⚠ E4 点名的那 5 条（**一条不许缺**）。顺序就是设计稿 §4 D3 的顺序。
RUNTIME_KEYS_UNITV3: tuple[str, ...] = (
    "_DIRT",
    "_DirtProgress",
    "_POTATO_MOD",
    "_AOStrange/_NormalStrange",
    "track_uv_scroll",
)


# --------------------------------------------------------------------------- #
# 标量绑定（§7.1 的"标量 → Principled"那一列）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ScalarBinding:
    """游戏 shader 的一个浮点参数 ↔ Principled 的一个输入。"""

    param: str
    blender_input: str
    invert: bool = False
    note: str = ""

    def apply(self, value: float) -> float:
        return (1.0 - value) if self.invert else value


#: 无 Principled 输入、**只要求"别丢"**的那个标量（见 §4 D3 / N1）。
#: 依据：HDRP `Runtime/Material/Material.hlsl:58-69` —— 该 uniform=1 时 specular 乘 1.0、
#: 只有 diffuse 乘 alpha；
#: 而 Blender 的 alpha 混合把镜面也乘 alpha。`Lit.shader:330` 定义了
#: `SUPPORT_BLENDMODE_PRESERVE_SPECULAR_LIGHTING` ⇒ HDRP/Lit 一定启用。
#: 实测：11,902 个真材质里 **8734 个（73%）** 该值 = 1。
#: ⚠ 它的 `blender_input` **不是** Principled 的输入名 —— 也**不在** `PARAM_TO_NODE_INPUT` 里，
#: 所以它不会驱动渲染（补那个补偿是另一件事，本版没做），只保证写侧别把它丢掉。
#: HDRP/Lit 的第二批真标量（★ 2026-10-17 第二轮「能接的都接上」）：
#:   * `_Ior`（Lit.shader:109，Range 1.0-2.5）→ Principled **IOR**，1:1；实测 93 颗透明材质里
#:     1.0(61) / 1.5(31) / 1.624(1)。
#:   * `_TransmissionEnable`（Lit.shader:168，ToggleUI 默认 **1.0**）→ **只在透射那档接**
#:     （插件侧 `_apply_transmission` 判 alpha_mode=BLEND），⛔ 不直接写 socket —— 写进去会把
#:     725 颗不透明材质全变成玻璃。
#:   * `_IridescenceThickness` / `_IridescenceMask`（Lit.shader:68/71，Range 0..1）→ 由插件侧
#:     `_apply_iridescence` 过 remap 与 ×1000 之后写 **Thin Film Thickness**。
#:   ⚠ 后三个**不走** `_apply_scalars`（见插件侧 `_PLUGIN_WIRED_SCALARS`），列在这里只为让
#:     `plan.scalars` 把游戏原值带到 Blender 侧。
_HDRP_LIT_EXTRA_SCALARS: tuple[ScalarBinding, ...] = (
    ScalarBinding("_Ior", "IOR"),
    ScalarBinding("_TransmissionEnable", "Transmission Weight（只给透射档，见插件）"),
    ScalarBinding("_IridescenceThickness", "Thin Film Thickness（×1000 成 nm，见插件）"),
    ScalarBinding("_IridescenceMask", "Thin Film Mask（并进厚度，见插件）"),
    #: alpha 的重映射（`LitData.hlsl:233` `lerp(_AlphaRemapMin, _AlphaRemapMax, alphaTex)`）。
    #: 绝大多数是 0/1（= 原样），列出来是为了**有读数**再决定接不接；不映射到任何 socket。
    ScalarBinding("_AlphaRemapMin", "Alpha Remap Min（并进贴图 alpha，见插件）"),
    ScalarBinding("_AlphaRemapMax", "Alpha Remap Max（并进贴图 alpha，见插件）"),
)
_PRESERVE_SPECULAR = ScalarBinding(
    "_EnableBlendModePreserveSpecularLighting",
    "（无 Principled 输入：只原样保留，不做补偿）",
    note="保留 ≠ 还原：Blender 侧镜面仍会被 alpha 衰减（差 ≈1/alpha）",
)

#: 标量 = **逐 shader 一份**（⛔ 2026-10-07 更正：原来只有一份 `_COMMON_SCALARS` 被四张表共用，
#: 名字全按 HDRP/Lit 填 —— 而一个 shader 只能读它 **Properties 块里声明过**的属性）。
#: 复算：`_work/audit-material/tools/probe_a_shader_params.py` ——
#: 改前缺 5/6（UnitV3）、6/6（Infantry）、0/6（HDRP/Lit）；改后**全 0**。
#: 真件里那些"多出来的"键是**换 shader 留下的死数据**
#: （例：`RU_T72B 1` 同时有 `_Metallic=0.0` 与 `_Metal=1.0`）。
_HDRP_LIT_SCALARS: tuple[ScalarBinding, ...] = (
    ScalarBinding("_Metallic", "Metallic"),
    ScalarBinding("_Smoothness", "Roughness", invert=True, note="Roughness = 1 − Smoothness"),
    ScalarBinding("_NormalScale", "Normal Map.Strength"),
    ScalarBinding("_AlphaCutoff", "Alpha（配合 blend_method=CLIP）"),
    ScalarBinding("_EmissiveIntensity", "Emission Strength"),
    ScalarBinding("_CoatMask", "Coat Weight"),
    _PRESERVE_SPECULAR,
    #: 第二批真标量定义在下面（`_HDRP_LIT_EXTRA_SCALARS`）—— 这里用名字转发，
    #: ⛔ 别把定义搬上来：那段注释是读数出处，和定义必须挨着。
    *_HDRP_LIT_EXTRA_SCALARS,
)

#: UnitV3 的真名（`unity-ba-rip-all/Assets/Shader/Shader Graphs_UnitV3.shader`）：
#: `_Smoothness`:6 · `_NormalStrange`:7 · `_Metal`:10 ·
#: `_EmissionStrange`:12 · `_AlphaClipThreshold`:15。

#: ⚠ `_Metal` 声明为 `Range(0, 2)`，与 Principled 的 Metallic(0..1) 不同域 ⇒ 本表只做
#: **原样记录 + 写回**，不驱动渲染
#: （skip 名单：`blender_addon/materials.py::_NON_RENDERING_SCALARS`）。
_UNITV3_SCALARS: tuple[ScalarBinding, ...] = (
    ScalarBinding("_Metal", "Metallic"),
    ScalarBinding("_Smoothness", "Roughness", invert=True, note="Roughness = 1 − Smoothness"),
    ScalarBinding("_NormalStrange", "Normal Map.Strength"),
    ScalarBinding("_AlphaClipThreshold", "Alpha（配合 blend_method=CLIP）"),
    ScalarBinding("_EmissionStrange", "Emission Strength"),
    #: ★ 2026-10-17：涂装层（desc='Camo'）自己的 UV 偏移 —— 逐套皮肤都不同
    #: （skin1 -0.004/-0.004 · skin2 -0.2/-0.11 · skin3 0.6/-0.004），⇒ 必须先带上再谈混合。
    ScalarBinding("_OffsetX", "涂装层 U 偏移（并进 Mapping，见插件）"),
    ScalarBinding("_OffsetY", "涂装层 V 偏移（并进 Mapping，见插件）"),
    ScalarBinding("_Layer_step", "涂装层步进（未用，只搬运）"),
    _PRESERVE_SPECULAR,
)

#: Infantry 的真名（`Shader Graphs_Infantry Shader.shader`）：`_Normal_Str` / `_AlphaClipping`。
#: 它的金属度 / 光滑度来自 `_Mask` 的通道（见本表 mask_slots），**没有**标量金属度。
_INFANTRY_SCALARS: tuple[ScalarBinding, ...] = (
    ScalarBinding("_Normal_Str", "Normal Map.Strength"),
    ScalarBinding("_AlphaClipping", "Alpha（配合 blend_method=CLIP）"),
    _PRESERVE_SPECULAR,
)

#: `HDRP/Unlit` 的真标量 —— ⛔ **别照 M1 抄**：这个 shader 一共只声明了两条，实测只用到一条。
#: 声明依据（`com.unity.render-pipelines.high-definition@…/Runtime/Material/Unlit/Unlit.shader`，
#: HDRP 14.0.12 与 17 两份同构）：`:41` `_AlphaCutoff("Alpha Cutoff", Range(0.0, 1.0))` ·
#: `:19` `_EmissiveIntensity`。
#: 读数（`_work/audit-full/materials.jsonl` 的 26 颗真材质）：
#:   * `_AlphaCutoff` 出现 **26/26**、**4 个不同值**（0.0 / 0.097 / 0.118 / 0.5）⇒ 逐颗调过；
#:   * `_EmissiveIntensity` **没有读数**：审计白名单只采 11 个浮点
#:     （`_CullMode` `_BlendMode` `_SurfaceType` `_Smoothness` `_Metallic` `_Cutoff` `_AORemapMax`
#:     `_AORemapMin` `_AlphaCutoff` `_NormalScale` `_Metal`）—— 里面没有它
#:     ⇒ **既不能当"存在"也不能当"不存在"** ⇒ 不加（缺证据就是不写）。
#: ⛔ 另外 5 个 M1 的标量（`_Metallic` / `_Smoothness` / `_NormalScale` / `_AORemapMin` /
#: `_AORemapMax`）在这 26 颗里 **26/26 都有值**，但 Unlit.shader 的 Properties 块（`:3-98`）
#: **一个都没声明** ⇒ 换 shader 留下的**死数据**（本模块开头那段讲的就是这个坑）⇒ 不加。
_UNLIT_SCALARS: tuple[ScalarBinding, ...] = (
    ScalarBinding("_AlphaCutoff", "Alpha（配合 blend_method=CLIP）"),
)

#: `HDRP/Decal` 的真标量。声明依据（`…/Runtime/Material/Decal/Decal.shader`）：
#: `:39` `_Smoothness` · `:40` `_Metallic` —— 原文注释
#: **"Alternative when no mask map is provided"**
#: （没有 `_MaskMap` 时才用这两条）。⇒ 与 `mask_slots` **不冲突**：有 MaskMap 走通道、
#: 没有走标量，同一张表里两条都得留着才对（`DecalData.hlsl:80-95` 的 `#else` 分支）。
#: 读数（81 颗真材质）：`_Smoothness` **81/81**、**11 个不同值**（0.0 … 0.712）⇒ 真有逐颗读数；
#: `_Metallic` **81/81**、**全部 0.0**（= 声明默认）⇒ 照抄，不猜。
_DECAL_SCALARS: tuple[ScalarBinding, ...] = (
    ScalarBinding("_Metallic", "Metallic"),
    ScalarBinding("_Smoothness", "Roughness", invert=True, note="Roughness = 1 − Smoothness"),
)


# --------------------------------------------------------------------------- #
# 映射表
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class MaterialTable:
    """一张映射表 = 一个 shader 的槽位/标量/运行时项口径。"""

    key: str
    shader: str
    title: str
    #: 精确槽名（Unity 的槽名就是这些）。
    base_color_slots: tuple[str, ...] = ()
    #: 前缀匹配的基色槽（UnitV3 的 Layer_<8 位 hash>）。
    base_color_prefixes: tuple[str, ...] = ()
    #: 由 **shader 自己写的 desc** 认颜色层（`m_ParsedForm.m_PropInfo.m_Props[].m_Description`）。
    #: ⛔ **不写死槽名**：槽名里那串 hash（Layer_A97CDC25）随 shader 变，**desc 才是契约**
    #: （与 `texture_install.COLOR_LAYER_DESCS` 同一份口径：'Albedo' / 'Camo'）。
    #: 判据顺序：**desc 优先，前缀兜底** —— 见 `plan_material(prop_descs=…)`。
    base_color_descs: tuple[str, ...] = ()
    normal_slots: tuple[str, ...] = ()
    #: MaskMap 同构的那张（R=金属度 / G=AO / B=细节 / A=光滑度）。
    mask_slots: tuple[str, ...] = ()
    emission_slots: tuple[str, ...] = ()
    #: 虹彩厚度层（HDRP/Lit 的 `_IridescenceThicknessMap`；R 通道）。
    iridescence_slots: tuple[str, ...] = ()
    #: 本 shader **真的声明了**的标量（⛔ 默认空 —— 不给"共用一份"的机会，必须逐表点名）。
    scalars: tuple[ScalarBinding, ...] = ()
    #: 角色 -> **必须出现在 m_ValidKeywords 里**才允许接线的那几个关键词。
    #: 语义：Unity 里"关键词不在 valid 里"= 这个变体没开（不在 invalid 里也一样是关的）。
    #: ⚠ 只在**实测到不一致**的角色上加门（见各表的读数）；没量到不一致的角色不加，
    #: 免得把"没声明"误当成"关着"。
    gates: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    runtime_items: tuple[RuntimeItem, ...] = ()
    unsupported: bool = False
    note: str = ""

    @property
    def known_slots(self) -> tuple[str, ...]:
        """表里认得的名（用于算"已赋但没映射"）。"""
        return (
            self.base_color_slots
            + self.normal_slots
            + self.mask_slots
            + self.emission_slots
            + self.iridescence_slots
        )

    def color_slots_from_descs(self, prop_descs: Mapping[str, str]) -> tuple[str, ...]:
        """**纯函数**：shader 自写的 `{槽名: desc}` -> 本表的**颜色层槽名**（按给定顺序）。

        ⛔ 只认 desc 认得出的那些：读不到 desc / desc 不在 `base_color_descs` 里 ⇒
        这个槽**不算**颜色层。`Normal` / `Mask` 就是靠这条挡住的 ——
        它们同样叫 `Layer_<hash>`，**按前缀认会把法线层当成颜色层**。
        """
        wanted = set(self.base_color_descs)
        if not wanted:
            return ()
        return tuple(str(slot) for slot, desc in prop_descs.items() if str(desc) in wanted)

    def matches(self, slot: str) -> bool:
        return slot in self.known_slots or any(
            slot.startswith(prefix) for prefix in self.base_color_prefixes
        )

    def role_of(self, slot: str) -> str | None:
        if slot in self.base_color_slots or any(
            slot.startswith(prefix) for prefix in self.base_color_prefixes
        ):
            return ROLE_BASE_COLOR
        if slot in self.normal_slots:
            return ROLE_NORMAL
        if slot in self.mask_slots:
            return ROLE_MASK
        if slot in self.emission_slots:
            return ROLE_EMISSION
        if slot in self.iridescence_slots:
            return ROLE_IRIDESCENCE
        return None

    def find_scalar(self, param: str) -> ScalarBinding | None:
        return next((s for s in self.scalars if s.param == param), None)

    def allows(self, role: str, enabled_keywords: Container[str]) -> bool:
        """这个角色在当前 shader 变体下**允许接线**吗（没有门 = 允许）。"""
        wanted = self.gates.get(role)
        if not wanted:
            return True
        return all(word in enabled_keywords for word in wanted)


#: **M1 · HDRP/Lit**（514 个材质：履带 / 枪械 / 飞行员 / 座舱）。
#: §7.1 第一行：_BaseColorMap **与** _MainTex → Base Color（实测两者指向同一张）。
M1_HDRP_LIT = MaterialTable(
    key=TABLE_M1,
    shader="HDRP/Lit",
    title="HDRP/Lit（履带 / 枪械 / 飞行员 / 座舱）",
    base_color_slots=("_BaseColorMap", "_MainTex", "_UnlitColorMap"),
    normal_slots=("_NormalMap",),
    mask_slots=("_MaskMap",),
    emission_slots=("_EmissiveColorMap",),
    #: ★ 2026-10-17：虹彩厚度层（实测 68 颗绑了图，且都满足 `_MATERIAL_FEATURE_IRIDESCENCE`）。
    iridescence_slots=("_IridescenceThicknessMap",),
    scalars=_HDRP_LIT_SCALARS,
    #: 只门控**实测到不一致**的那一条：`_NormalMap` 已绑但 `_NORMALMAP` 没开 = 实测 3 个
    #: （`M2.mat`/`M2_0.mat`/`M2_1.mat`）。另三处实测**0 不一致** ⇒ 不加门：
    #: `_MaskMap` 已绑 ≡ `_MASKMAP` 在 valid（1216/1216）、
    #: `_EmissiveColorMap` ≡ `_EMISSIVE_COLOR_MAP`（14/14）。
    #: 读数：`_work/audit-material/tools/probe_f_keyword_gate.py`。
    gates={ROLE_NORMAL: ("_NORMALMAP",)},
    runtime_items=(_RUNTIME_TRACK_SCROLL,),
    note="_BaseColorMap 与 _MainTex 实测同指一张；两个都赋且不一致时按 _BaseColorMap 优先并留痕",
)

#: **M2 · Shader Graphs/UnitV3**（1,655 个材质：坦克 / 直升机 / 车辆主体）。
#: §7.3 主路：Layer_<hash> **直接当 Base Color**（不是"近似"）。
M2_UNITV3 = MaterialTable(
    key=TABLE_M2,
    shader="Shader Graphs/UnitV3",
    title="Shader Graphs/UnitV3（坦克 / 直升机 / 车辆主体）",
    base_color_prefixes=("Layer_",),
    #: 实测（2026-10-10）：UnitV3 的 Layer_A97CDC25 的 desc = 'Albedo'，**只有这一个**颜色层
    #: （另一件材质只有 'Albedo' —— 见 texture_install.COLOR_LAYER_DESCS 的读数）。
    #: desc 认得出就走 desc；认不出（没有 shader 树）才退回上面那个前缀。
    base_color_descs=(DESC_ALBEDO,),
    normal_slots=("_VirtualDetailNormal",),
    emission_slots=("_Emission",),
    scalars=_UNITV3_SCALARS,
    #: 自发光由 `[Toggle(_EMISSONENABLE)]` 门控（`Shader Graphs_UnitV3.shader:9`）。
    #: 实测：`_Emission` 已绑 3721 个真材质，其中 **2175 个开关没开**（关键词不在 valid）
    #: ⇒ 不加门就会在 Blender 里发光、游戏里不发光。
    #: 读数：`_work/audit-material/tools/probe_f_keyword_gate.py`。
    gates={ROLE_EMISSION: ("_EMISSONENABLE",)},
    runtime_items=(
        _RUNTIME_DIRT,
        _RUNTIME_DIRT_PROGRESS,
        _RUNTIME_POTATO,
        _RUNTIME_STRANGE,
    ),
    note="有效贴图槽 = Layer_<8 位 hash>（普通 2D，非 VT；已绑 5006）"
    "+ _Emission（3721，受 _EMISSONENABLE 门控）；"
    "Normal/Mask 是 **VT 层**（Layer_8D60137F / Layer_4C4561B0），静态资产里取不到"
    "（11902 个真材质的 m_TexEnvs 里 Layer_4C4561B0 命中 0）⇒ 金属度/粗糙度只能取标量",
)

#: **M3 · Shader Graphs/Infantry Shader**（66 个材质：步兵）。
#: §7.1 里" _Mask 需实测确认是否 MaskMap 同构" —— **实测确认同构**：
#: low_to_tex_OCP_MaskMap 的 R mean 3.06（98%≈0）/ G 214.84 / B 4.98 / A 94.36，
#: 与 HDRP _MaskMap 的通道约定逐条一致（见 tests/test_materials.py 的 E2 断言）。
M3_INFANTRY = MaterialTable(
    key=TABLE_M3,
    shader="Shader Graphs/Infantry Shader",
    title="Shader Graphs/Infantry Shader（步兵）",
    base_color_slots=("_Albedo",),
    normal_slots=("_Normal",),
    mask_slots=("_Mask",),
    scalars=_INFANTRY_SCALARS,
    runtime_items=(_RUNTIME_POTATO,),
    note="_Mask 与 HDRP 的 _MaskMap 通道同构（R 金属度 / G AO / B 细节 / A 光滑度），实测确认",
)

#: **M4 · `Shader Graphs/UnitV4_Camo`** —— **DLC2 那批车（含 CV-9035）用的就是它**。
#:
#: ## ⛔ 改这一段之前先读这三处（别凭印象改）
#:   * `kb/18 §6 渲染` —— 车体/炮塔/爆反走 UnitV3；DLC 新车走 UnitV4_Camo。
#:   * `kb/21 §6 贴图方向契约` —— `Layer_<8 位 hash>` = **颜色层**；`_Emission` = 自发光。
#:   * `kb/22`（UnitV3 档案 · §M2 槽位表）—— UnitV3 与 UnitV4_Camo 的**槽名同构**。
#:
#: ## 2026-10-11 改口径：颜色层改由 **shader 自己写的 desc** 认（销 `kb/19` §5 的 E4 缺口）
#:   真读数（`_work/camo/判据与读数.md` §1.1 —— 从 `m_ParsedForm.m_PropInfo.m_Props[]` 读的）：
#:     * `Layer_A97CDC25`   desc = **'Albedo'** —— 底图
#:     * `Layer_A97CDC25_1` desc = **'Camo'**   —— 涂装层（自己的平铺 `_CamoTiling` 等**没被引用**）
#:     * `Layer_8D60137F`   desc = 'Normal' · `Layer_4C4561B0` desc = 'Mask' ← **VT 层**
#:   ⛔ 旧口径 `base_color_prefixes=("Layer_",)` **会把后两条也认成颜色层**（它们同样叫
#:   `Layer_<hash>`）—— **desc 是 shader 的契约，槽名 hash 不是** ⇒ 本表**只留 desc**。
#:   比例读数（`_work/p0census/slots.json` 的 57 颗真材质）：desc 规则 **57/57（100%）** 认出颜色层
#:   （每颗 2 个：Albedo 57/57 有图 ＋ Camo 57/57 有图）；**空表那版是 0/57（0%）**。
#:   ⚠ 其余槽（`_Emission` / `_VirtualDetailNormal`）在这 57 颗里**一个都没绑** ——
#:   ⛔ 别照 UnitV3 的样子推断过去。
#:   ⛔ 改槽之前记住：**颜色槽只能放 `m_ColorSpace=1`（sRGB）的图**；放 Linear 数据图
#:   （法线/遮罩）会把**整个车身抠成透明** —— 见 `skin_install.guard_slot_target` 的实测记录。
M4_UNITV4_CAMO = MaterialTable(
    key=TABLE_M4,
    shader="Shader Graphs/UnitV4_Camo",
    title="Shader Graphs/UnitV4_Camo（DLC2 车族：底图 + 涂装层）",
    base_color_descs=(DESC_ALBEDO, DESC_CAMO),
    scalars=_UNITV3_SCALARS,
    unsupported=True,
    note="颜色层由 shader 自写 desc 认（Albedo / Camo，2026-10-11 起；槽名 hash 一个字都不写）——"
    "真读数 _work/camo/判据与读数.md §1.1；57 颗真材质 desc 规则 57/57 认出（空表那版 0/57）。"
    "unsupported=True 保留：『还原计划不展开它』这条是 2026-10-10 的口径，不属 E4 那一半；"
    "两槽都是普通 2D 颜色图，但 Normal/Mask 仍是 VT 层（静态取不到）。"
    "⛔ 颜色槽只放 m_ColorSpace=1 的图（见 skin_install.guard_slot_target）",
)

#: **M5 · `HDRP/Unlit`** —— 26 颗真材质（标点 / 出生点标签 / 占领区圈 / `LODs` LOD 代理）。
#:
#: ## 为什么补这张表（判据一行）
#: 补之前 `table_for("HDRP/Unlit")` = None ⇒ `plan_material` 给 `TABLE_NONE` /
#: `COVERAGE_UNKNOWN` / `links=()` ⇒ **一条线都接不上**，而这 26 颗里 **24 颗真带图**。
#: 补之后：`table_key` = M5、`coverage` = 完整、`links` 含 `base_color -> Base Color`。
#:
#: ## 读数（`_work/audit-full/materials.jsonl`，5,431 行按 `shader` 过滤；本家族 26 颗）
#:   * `_UnlitColorMap` 带图 **24/26**（出现 26）· `_MainTex` 带图 **24/26**（出现 26）——
#:     两槽在 **24/24 颗双绑材质里指向同一个 pathID**（diff=0；另 2 颗两槽都空）
#:     ⇒ 先后不影响取到哪张图。表里把 `_UnlitColorMap` 放前面 = 口径上的主槽
#:     （shader 里它是 `[MainTexture]`）。
#:   * `_EmissiveColorMap` 带图 **0/26** · `_DistortionVectorMap` 带图 **0/26** ——
#:     两个槽都照 shader 的声明列进来，但这一族**一颗都没绑** ⇒ emission 角色不会出现（如实）。
#:   * `_NormalMap` / `_MaskMap`：Unlit.shader 的 Properties 块（`:3-98`）**没声明**，
#:     这 26 颗里出现次数也是 **0/26** ⇒ ⛔ 不进表（没证据的槽一个都不加）。
#:
#: ## shader 侧依据
#: `…/Runtime/Material/Unlit/Unlit.shader`：`:7` `[MainTexture] _UnlitColorMap` ·
#: `:91` `[HideInInspector] _MainTex`（原文：GI 烘焙只认这几个硬编码名，Unity 把同一张图再存一份
#: 给它）· `:12` `_EmissiveColorMap` · `:22` `_DistortionVectorMap`。
#:
#: ## ⛔ 没加的（逐类写清为什么）
#: * `_Metallic` / `_Smoothness` / `_NormalScale` / `_AORemapMin` / `_AORemapMax`：材质里
#:   26/26 有值，但 shader **没声明** ⇒ 死数据（读数见 `_UNLIT_SCALARS`）。
#: * `_Cutoff`：shader `:93` 声明了（同样是 GI 烘焙的镜像），26/26 有值，但**与 `_AlphaCutoff`
#:   逐颗相同**（26/26 差 0）⇒ 再加一条就是把同一个 Alpha 输入绑两次 ⇒ 不加。
#: * gates：本家族没有"槽绑了图但关键词没开"的组合可比（`_EmissiveColorMap` 0/26 带图、
#:   基色槽没有对应关键词）⇒ 不加门（门只加在实测到不一致的角色上）。
#: * runtime_items：这 26 颗没量到运行期改材质的证据 ⇒ 不加。
M5_HDRP_UNLIT = MaterialTable(
    key=TABLE_M5,
    shader="HDRP/Unlit",
    title="HDRP/Unlit（无光照标记面：标点 / 出生点 / 占领区圈 / LOD 代理）",
    base_color_slots=("_UnlitColorMap", "_MainTex"),
    emission_slots=("_EmissiveColorMap",),
    scalars=_UNLIT_SCALARS,
    note="_UnlitColorMap 与 _MainTex 实测 24/24 双绑材质同指一张（_MainTex 是 GI 烘焙的镜像槽）",
)

#: **M6 · `HDRP/Decal`** —— 81 颗真材质（道路线 / 停机坪涂装 / 污渍 / 破损贴花）。
#:
#: ## 为什么补这张表
#: 补之前同样一条线都接不上；而 **78/81 颗真的绑了 `_BaseColorMap`**、42 颗绑了法线、
#: 20 颗绑了 `_MaskMap`。
#:
#: ## 读数（`_work/audit-full/materials.jsonl`，本家族 81 颗）
#:   * `_BaseColorMap` 带图 **78/81** · `_NormalMap` 带图 **42/81** ·
#:     `_MaskMap` 带图 **20/81** · `_EmissiveColorMap` 带图 **0/81**
#:     （四个槽都照 shader 的声明进表；emission 这一族一颗都没绑 ⇒ 不会有 emission 连线）。
#:
#: ## 为什么不给 `_MATERIAL_AFFECTS_ALBEDO` 加门（2026-10-17 裁定，别再翻）
#:   · 读数：**78 颗**绑了 `_BaseColorMap`，其中 **5 颗**的 `_MATERIAL_AFFECTS_ALBEDO` 不在册
#:     （`Crack`×3 / `Misc` / `Dirt_Straight_OnlyNormal_Material`）；
#:     而 `_BaseColorMap` 绑 ⟺ `_COLORMAP` 在册 是 **78/78**（双向 0 例外）。
#:   · `_MATERIAL_AFFECTS_*` **不是**"槽绑没绑图"的谓词 —— 反例就在同一批 81 颗里：
#:     `_MATERIAL_AFFECTS_MASKMAP` 68 颗在册 / 20 颗绑图 · `..._NORMAL` 58 / 42
#:     ⇒ 拿它当门会把**真绑了图**的材质关掉 ⇒ 不能当门。
#:   · 真正对应的是 `_BaseColorMap 绑 ⟺ _COLORMAP 在册`（78/78）⇒ **0 不一致**
#:     ⇒ 按本模块口径（门只给实测到不一致的角色）**不加门**。
#:   · 那 5 颗在游戏里到底套不套 albedo **没有实测**（要 HDRP Decal 的 RT 变体实验）；
#:     `Decal/DecalData.hlsl:73-75` 那一支看着像"不混 albedo"，但**那个变体没验过**
#:     ⇒ ⛔ 不拿未验证的语义改行为（⛔ 也别把它写成用例里的契约）。
#:   · 另两个角色同读数 **0 不一致**：`_NormalMap` ⟺ `_NORMALMAP`（42/42）·
#:     `_MaskMap` ⟺ `_MASKMAP`（20/20）⇒ 不加。
#:
#: ## shader 侧依据（`…/Runtime/Material/Decal/Decal.shader`，HDRP 14.0.12 / 17 两份同构）
#: `:6` `[MainTexture] _BaseColorMap` · `:7` `_NormalMap` · `:8` `_MaskMap` ·
#: `:21` `_EmissiveColorMap` · `:39` `_Smoothness` · `:40` `_Metallic`。
#: `_MaskMap` 的通道口径（`Decal/DecalData.hlsl:80-95`，与 M1 同一份 HDRP 约定）：
#: R → Metallic、G → AO、A → Smoothness（`#else` 那支：没有 MaskMap 才取 `_Metallic`/`_Smoothness`）
#: ⇒ 正好复用 `NODE_SHAPE`。⚠ R/G 在 `DECALS_4RT` 变体下还要过一次
#: `_MetallicRemapMin/Max` / `_AORemapMin/Max` 的 lerp —— 这两组 remap **不在审计白名单里**、
#: 没有读数 ⇒ 本版不接线（默认值 0/1 等于直取，但那是 shader 默认值，不是实测）。
#:
#: ## ⛔ 没加的（逐类写清为什么）
#: * `_NormalScale` / `_AlphaCutoff` / `_Cutoff` / `_CullMode` / `_SurfaceType` / `_BlendMode`：
#:   这 81 颗里 **81/81 都有值**，但 Decal.shader 的 Properties 块（`:3-63`）**一个都没声明**
#:   （`_NormalScale` 全 HDRP 只有 `Lit/Lit.shader:27` 声明）⇒ 换表留下的死数据，
#:   取值也只有 1.0 / 1.088 两个，1.0 正是 Lit 的默认 ⇒ 一个都不加。
#: * `_AORemapMin` / `_AORemapMax`：shader `:32` / `:33` 声明了，81/81 有值，但**全部是默认
#:   0.0 / 1.0**，且 `NODE_SHAPE` 里 AO 那一条本来就标 `ignored`（Principled 没有 AO 输入）
#:   ⇒ 加了也传不到任何节点 ⇒ 不加。
#: * `_EmissiveIntensity`：`:24` 声明了，但审计白名单没采它（见 `_UNLIT_SCALARS` 那 11 个浮点）
#:   ⇒ **没有读数** ⇒ 不加。
#: * gates：**裁定不加**（见上面「为什么不给 _MATERIAL_AFFECTS_ALBEDO 加门」那一节）。
#: * runtime_items：这 81 颗没量到运行期改材质的证据 ⇒ 不加。
M6_HDRP_DECAL = MaterialTable(
    key=TABLE_M6,
    shader="HDRP/Decal",
    title="HDRP/Decal（地面贴花：道路线 / 停机坪涂装 / 污渍 / 破损）",
    base_color_slots=("_BaseColorMap",),
    normal_slots=("_NormalMap",),
    mask_slots=("_MaskMap",),
    emission_slots=("_EmissiveColorMap",),
    scalars=_DECAL_SCALARS,
    note="_MaskMap 复用 M1 的通道口径（R 金属 / G AO / A 光滑，见 DecalData.hlsl:80-95）",
)

#: shader 名 -> 表。**只有这一份**。
TABLES: Mapping[str, MaterialTable] = {
    M1_HDRP_LIT.shader: M1_HDRP_LIT,
    M2_UNITV3.shader: M2_UNITV3,
    M3_INFANTRY.shader: M3_INFANTRY,
    M4_UNITV4_CAMO.shader: M4_UNITV4_CAMO,
    M5_HDRP_UNLIT.shader: M5_HDRP_UNLIT,
    M6_HDRP_DECAL.shader: M6_HDRP_DECAL,
}


def table_for(shader: str) -> MaterialTable | None:
    """shader 名 -> 映射表（认不出来返回 None，**不猜**）。"""
    return TABLES.get((shader or "").strip())


# --------------------------------------------------------------------------- #
# 还原计划
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class SlotBinding:
    """计划里的一条槽绑定：角色 + 槽名 + 贴图 pathID（None = 槽在但没赋图）。"""

    role: str
    slot: str
    texture_id: int | None

    @property
    def assigned(self) -> bool:
        return self.texture_id is not None


@dataclass(frozen=True, slots=True)
class MaterialPlan:
    """一个材质的还原计划（纯数据；Blender 侧照着它建节点）。"""

    material_name: str
    shader: str
    table_key: str
    coverage: str
    slots: tuple[SlotBinding, ...] = ()
    scalars: tuple[tuple[str, float], ...] = ()
    links: tuple[ChannelLink, ...] = ()
    unmapped_slots: tuple[str, ...] = ()
    runtime_items: tuple[RuntimeItem, ...] = ()
    alpha_mode: str = "OPAQUE"
    notes: tuple[str, ...] = ()
    #: 由 **shader 自写 desc** 认出来的颜色层槽名（E4；没给 `prop_descs` 时是空的）。
    desc_color_slots: tuple[str, ...] = ()
    #: `m_ValidKeywords`（**开着**的词；Unity 的语义：不在 valid 里就是关的）。
    #: 插件侧的门（虹彩 / 自发光）就靠它 —— 实测 725 颗 HDRP/Lit 里只有 **70 颗**
    #: 带 `_MATERIAL_FEATURE_IRIDESCENCE`，而 `_IridescenceThickness` 默认值 1.0 是 724 颗都有。
    keywords: tuple[str, ...] = ()
    #: 游戏材质自己写的**颜色**（原样搬运：`_BaseColor` / `_UnlitColor` / `_Color`，RGBA 四通道）。
    #: ★ 2026-10-17：玻璃类材质（实测 196 颗）**没有基色贴图**，颜色与透明度全在这几个里
    #: （例 `DLC2_UH60_glass` = (0.537, 0.537, 0.467, **0.051**)）—— 不接就是默认灰、玻璃看不见。
    colors: tuple[tuple[str, tuple[float, float, float, float]], ...] = ()

    def slot_of(self, role: str) -> SlotBinding | None:
        """按角色取**第一个已赋图**的槽（都没赋就返回第一个同角色槽）。"""
        same = [s for s in self.slots if s.role == role]
        return next((s for s in same if s.assigned), same[0] if same else None)

    def scalar(self, param: str) -> float | None:
        return next((v for name, v in self.scalars if name == param), None)

    def color(self, param: str) -> tuple[float, float, float, float] | None:
        """游戏材质里那颗颜色（没有就 None —— 调用方回退，⛔ 不编默认值）。"""
        return next((v for name, v in self.colors if name == param), None)

    def base_color_rgba(self) -> tuple[float, float, float, float] | None:
        """基色那颗颜色：按 `COLOR_TO_NODE_INPUT` 的顺序取**第一个存在**的。"""
        for param in COLOR_TO_NODE_INPUT:
            found = self.color(param)
            if found is not None:
                return found
        return None

    @property
    def clip_threshold(self) -> float | None:
        """alpha clip 的阈值 —— **按逻辑输入名找**，不按参数名找。

        为什么要有它：`_AlphaCutoff` 是 HDRP/Lit 的名字，UnitV3 叫 `_AlphaClipThreshold`、
        Infantry 叫 `_AlphaClipping`（见各自的标量表）。调用方（Blender 侧写
        `material.alpha_threshold`）原来硬写 `plan.scalar("_AlphaCutoff")`，
        换个 shader 就静默变 None。
        ⚠ 本属性是给那边用的**接口**；把 `blender_addon/materials.py::_apply_alpha_mode` 改成用它，
        是 blender_addon 范围内的一处跟进（本次没改）。
        """
        return self._scalar_with_input_prefix("Alpha")

    def _scalar_with_input_prefix(self, prefix: str) -> float | None:
        """按**逻辑输入名**取标量 —— 参数名逐 shader 不同（见四张表的标量清单）。"""
        for name, value in self.scalars:
            binding = None
            for table in TABLES.values():
                binding = table.find_scalar(name)
                if binding is not None:
                    break
            if binding is not None and binding.blender_input.startswith(prefix):
                return value
        return None

    @property
    def normal_scale(self) -> float | None:
        """Normal Map 节点的 Strength —— 同样**按逻辑输入名找**。

        为什么：`_NormalScale` 是 HDRP/Lit 的名字，UnitV3 叫 `_NormalStrange`、
        Infantry 叫 `_Normal_Str`。Blender 侧原来硬写 `plan.scalar("_NormalScale")`
        ⇒ 换个 shader 就静默变 None，Strength 停在 Blender 默认 1.0（不算错值，但不够忠实）。
        """
        return self._scalar_with_input_prefix("Normal Map.")

    @property
    def runtime_keys(self) -> tuple[str, ...]:
        return tuple(item.key for item in self.runtime_items)

    def row(self) -> str:
        return f"{self.material_name} | {self.shader} | {self.table_key} | {self.coverage}"


@dataclass(frozen=True, slots=True)
class UnitPlan:
    """一个单位（一辆车 / 一个兵）的全部材质计划 + 合并后的未还原清单。"""

    unit_key: str
    materials: tuple[MaterialPlan, ...] = ()
    #: 逐条去重的运行时项（跨材质合并）。
    runtime_items: tuple[RuntimeItem, ...] = ()
    #: 已赋但映射表里没有的槽（去重）。
    unmapped_slots: tuple[str, ...] = ()

    @property
    def runtime_count(self) -> int:
        return len(self.runtime_items)

    def coverage_on(self, shader: str) -> str:
        plans = [p for p in self.materials if p.shader == shader]
        if not plans:
            return COVERAGE_UNKNOWN
        return worst_coverage(p.coverage for p in plans)

    def summary(self) -> str:
        return (
            f"{self.unit_key}：材质 {len(self.materials)} / 静态还原度 "
            f"{'/'.join(sorted({p.coverage for p in self.materials})) or COVERAGE_UNKNOWN}"
            f" / 运行时项 {self.runtime_count} 条"
        )


#: 还原度从差到好（取最差的那个 = 保守口径）。
_COVERAGE_RANK = {
    COVERAGE_UNKNOWN: 0,
    COVERAGE_UNSUPPORTED: 1,
    COVERAGE_PARTIAL: 2,
    COVERAGE_FULL: 3,
}


def worst_coverage(values: Iterable[str]) -> str:
    return min(values, key=lambda v: _COVERAGE_RANK.get(v, 0), default=COVERAGE_UNKNOWN)


def _slots_of(material: Mapping[str, object]) -> tuple[tuple[str, int | None], ...]:
    out: list[tuple[str, int | None]] = []
    for entry in material.get("texture_slots") or ():
        try:
            slot, tid = entry  # type: ignore[misc]
        except (TypeError, ValueError):  # pragma: no cover -- 形状不对就跳过
            continue
        out.append((str(slot), None if tid is None else int(tid)))
    return tuple(out)


def _float_of(material: Mapping[str, object], param: str) -> float | None:
    floats = material.get("floats")
    if not isinstance(floats, Mapping) or param not in floats:
        return None
    try:
        return float(floats[param])  # type: ignore[arg-type]
    except (TypeError, ValueError):  # pragma: no cover
        return None


def _keywords_of(material: Mapping[str, object]) -> tuple[str, ...]:
    """**所有**出现过的关键词（valid ∪ invalid ∪ keywords）—— 只用于"报告里有哪些词"。

    ⛔ **不许拿它判"这个开关开没开"**：`m_InvalidKeywords` 是 Unity 记的**显式关闭**，
    并进来就把"关着"读成"开着"（实测：1510 个 HDRP/Lit 材质的 `_NORMALMAP` 在 invalid 里）。
    判开关一律用 `_enabled_keywords()`。
    """
    out: list[str] = []
    for key in ("valid_keywords", "invalid_keywords", "keywords"):
        for word in material.get(key) or ():
            text = str(word)
            if text not in out:
                out.append(text)
    return tuple(out)


def _enabled_keywords(material: Mapping[str, object]) -> frozenset[str]:
    """**开着**的关键词 = `m_ValidKeywords`（Unity 的语义：不在 valid 里就是关的）。

    回退：若调用方没给 `valid_keywords` 这一格（合成件 / 老调用方只给 `keywords`），
    才退到 `keywords` —— 有 valid 就**绝不**并 invalid。
    """
    words = material.get("valid_keywords")
    if words is None:
        words = material.get("keywords")
    return frozenset(str(word) for word in (words or ()))


def _alpha_mode(material: Mapping[str, object]) -> str:
    """透明优先，其次 alpha clip；否则 OPAQUE。

    2026-10-07 更正两处（都可复算，读数 `_work/audit-material/tools/probe_h_clip_priority.py`）：

    1. **顺序反了**：原来先看 `_ALPHATEST_ON`。但 HDRP 里"透不透"
       (`_SurfaceType`/`_SURFACE_TYPE_TRANSPARENT`)
       与"裁不裁"(`_ALPHATEST_ON`) 是**正交**的 —— "Transparent + Alpha Clipping" 是合法组合，
       它仍然是**混合**的。实测 **21 个** `_SurfaceType==1` 的真材质被判成 CLIP（例：`M-ATV_Glass`、
       `RU_3Ural4320Armor_Glass 1` —— 两个的 `_BlendMode` 都是 1 = 叠加）。
    2. **读了 union**：原来走 `_keywords_of`（valid ∪ invalid）⇒ 显式关闭的词会被当成开着。
       现在只看 `_enabled_keywords`。

    `_BlendMode`（HDRP：0=Alpha / 1=Additive / 4=Premultiply，见
    `com.unity.render-pipelines.high-definition@14.0.12/Runtime/Material/MaterialBlendModeEnum.cs:11-16`）
    是**三档**，而 Blender 的 `blend_method` 只有一档"混合" ⇒ 三档都塌成 BLEND，
    但**会在 notes 里留痕**（见 `plan_material`），不许静默。
    """
    enabled = _enabled_keywords(material)
    surface = _float_of(material, "_SurfaceType")
    transparent = (surface is not None and surface >= 0.5) or "_SURFACE_TYPE_TRANSPARENT" in enabled
    if transparent:
        return "BLEND"
    if "_ALPHATEST_ON" in enabled:
        return "CLIP"
    return "OPAQUE"


def plan_material(
    material: Mapping[str, object],
    *,
    table: MaterialTable | None = None,
    prop_descs: Mapping[str, str] | None = None,
) -> MaterialPlan:
    """一个材质 -> 还原计划。

    :param table: 只在**正控**里用（故意装错映射表，看判据能不能红）。默认按
        material["shader"] 查 TABLES；认不出来 = 未识别，不猜。
    :param prop_descs: **shader 自己写的** `{槽名: m_Description}`（读法见
        `texture_install.color_layers`）。给了就**desc 优先**认颜色层 ——
        槽名里的 hash 随 shader 变，**desc 才是契约**（E4）；认不出才退回名字那套
        （前缀 / 精确槽名）。⛔ 不给它就等于"这颗 shader 的 desc 我们没读到"，
        那时颜色层只按表里写死的名字认。

    ⚠ 判据要先能红：plan_material(real_material, table=错表) 出来的计划必须让
    E2 的断言失败 —— 见 tests/test_materials.py::test_e5_wrong_table_turns_e2_red。
    """
    name = str(material.get("name") or "")
    shader = str(material.get("shader") or "")
    owned = table is None
    table = table or table_for(shader)
    notes: list[str] = []

    assigned_slots = [slot for slot, tid in _slots_of(material) if tid is not None]

    if table is None:
        return MaterialPlan(
            material_name=name,
            shader=shader,
            table_key=TABLE_NONE,
            coverage=COVERAGE_UNKNOWN,
            slots=tuple(SlotBinding("", slot, tid) for slot, tid in _slots_of(material)),
            unmapped_slots=tuple(assigned_slots),
            alpha_mode=_alpha_mode(material),
            notes=(f"没有 {shader!r} 的映射表（不猜）",),
            colors=tuple(
                (param, tuple(float(ch) for ch in value))
                for param, value in (material.get("colors") or {}).items()
                if param in (*COLOR_TO_NODE_INPUT, *COLOR_CARRY_ONLY) and len(tuple(value)) == 4
            ),
            keywords=tuple(str(word) for word in (material.get("valid_keywords") or ())),
        )

    # E4：**desc 优先** —— 槽名里的 hash 会被 shader 换掉，desc 是 shader 自己写的契约。
    # ⚠ 只留**这颗材质真的有**的那些槽：shader 上声明了 Camo 层，材质里没有那一格就不算它。
    present = {slot for slot, _tid in _slots_of(material)}
    desc_slots = (
        tuple(
            slot
            for slot in table.color_slots_from_descs(prop_descs or {})
            if slot in present
        )
        if prop_descs
        else ()
    )
    desc_set = set(desc_slots)
    if desc_slots:
        notes.append(
            "颜色层由 shader 自写 desc 认："
            + "、".join(f"{slot}={str((prop_descs or {}).get(slot))}" for slot in desc_slots)
        )
    # ⚠ desc == 'Camo' 的那一格**单独给一个角色**（`ROLE_CAMO`）：它和底图不是同一层，
    #   接法也不同（自带平铺/偏移 + 近似混合权重），⛔ 不许把它当第二条 base_color 挤掉底图。
    desc_of = dict(prop_descs or {})
    slots = tuple(
        SlotBinding(
            (
                ROLE_CAMO
                if desc_of.get(slot) == DESC_CAMO
                else (
                    ROLE_BASE_COLOR if slot in desc_set else (table.role_of(slot) or "")
                )
            ),
            slot,
            tid,
        )
        for slot, tid in _slots_of(material)
    )

    # _BaseColorMap 与 _MainTex 同时赋且指向不同贴图 ⇒ 留痕（"与"是实测事实，不是保证）。
    if owned:
        primary = next(
            (s for s in slots if s.role == ROLE_BASE_COLOR and s.slot == "_BaseColorMap"), None
        )
        alias = next(
            (s for s in slots if s.role == ROLE_BASE_COLOR and s.slot == "_MainTex"), None
        )
        if primary and alias and primary.texture_id != alias.texture_id:
            notes.append(
                "_BaseColorMap 与 _MainTex 指向不同贴图"
                f"（{primary.texture_id} vs {alias.texture_id}）⇒ 按 _BaseColorMap 优先"
            )

    unmapped = tuple(s.slot for s in slots if s.role == "" and s.assigned)

    colors: list[tuple[str, tuple[float, float, float, float]]] = []
    raw_colors = material.get("colors")
    if isinstance(raw_colors, Mapping):
        for param in (*COLOR_TO_NODE_INPUT, *COLOR_CARRY_ONLY):
            value = raw_colors.get(param)
            if value is None:
                continue
            try:
                channels = tuple(float(ch) for ch in value)
            except (TypeError, ValueError):
                continue
            if len(channels) != 4:
                continue
            colors.append((param, (channels[0], channels[1], channels[2], channels[3])))

    scalars: list[tuple[str, float]] = []
    for binding in table.scalars:
        value = _float_of(material, binding.param)
        if value is not None:
            scalars.append((binding.param, value))

    # 角色要**两个条件都满足**才算数：① 槽里赋了图；② 本 shader 变体的开关允许用它（gates）。
    # ②是 2026-10-07 补的：Unity 里"槽绑着图"≠"shader 用这张图"。
    enabled = _enabled_keywords(material)
    roles = {s.role for s in slots if s.assigned and table.allows(s.role, enabled)}
    links = tuple(link for link in NODE_SHAPE if link.role in roles)

    # 三档混合模式塌成一档 ⇒ 必须留痕（⛔ 不静默）。
    blend_mode = _float_of(material, "_BlendMode")
    if blend_mode is not None and blend_mode != 0.0:
        notes.append(
            f"HDRP _BlendMode={blend_mode:g}（1=Additive / 4=Premultiply）—— "
            "Blender 的 blend_method "
            "只有一档混合 ⇒ 按 BLEND 近似，叠加/预乘的差别没还原"
        )

    if table.unsupported:
        coverage = COVERAGE_UNSUPPORTED
        notes.append(table.note)
    else:
        coverage = COVERAGE_PARTIAL if unmapped else COVERAGE_FULL

    return MaterialPlan(
        material_name=name,
        shader=shader,
        table_key=table.key,
        coverage=coverage,
        slots=slots,
        scalars=tuple(scalars),
        links=links,
        unmapped_slots=unmapped,
        runtime_items=table.runtime_items,
        alpha_mode=_alpha_mode(material),
        notes=tuple(notes),
        desc_color_slots=desc_slots,
        colors=tuple(colors),
        keywords=tuple(str(word) for word in (material.get("valid_keywords") or ())),
    )


def plan_unit(unit_key: str, materials: Iterable[Mapping[str, object]]) -> UnitPlan:
    """一个单位的全部材质 -> UnitPlan（运行时项与未映射槽跨材质合并、逐条去重）。"""
    plans = tuple(plan_material(m) for m in materials)
    runtime: list[RuntimeItem] = []
    seen_runtime: set[str] = set()
    unmapped: list[str] = []
    for plan in plans:
        for item in plan.runtime_items:
            if item.key not in seen_runtime:
                seen_runtime.add(item.key)
                runtime.append(item)
        for slot in plan.unmapped_slots:
            if slot not in unmapped:
                unmapped.append(slot)
    return UnitPlan(
        unit_key=unit_key,
        materials=plans,
        runtime_items=tuple(runtime),
        unmapped_slots=tuple(unmapped),
    )


#: W5 写回要用的「参数 ↔ 节点输入」对照（**只有一份**）。
#: 值 = (Blender 侧逻辑输入名, 写回时是否要反一次)。
#: ⚠ 这张表**同时**决定 Blender 侧写不写 socket（`blender_addon/materials.py::_apply_scalars`）
#: ⇒ 往里加名字 = 改渲染行为，不只是改写回。
#: 2026-10-07：补上 UnitV3 / Infantry 的**真名**（原来只有 HDRP/Lit 那一套）。
#: 颜色参数 ↔ Principled 的逻辑输入名（**顺序即优先级**：HDRP/Lit 用 `_BaseColor`，
#: HDRP/Unlit 用 `_UnlitColor`，少数老 shader 用 `_Color`）。
#: ⚠ 与 `PARAM_TO_NODE_INPUT` 分开：颜色是**四通道**，写回侧还没有颜色参数（⛔ 别硬塞进去）。
COLOR_TO_NODE_INPUT: Mapping[str, str] = {
    "_BaseColor": "Base Color",
    "_UnlitColor": "Base Color",
    "_Color": "Base Color",
}

#: **只搬运、不直接映射到某个 socket** 的颜色（插件侧按材质逻辑用）。
#:   * `_IridescenceThicknessRemap`（Vector，落在 m_Colors 里）：(min, max) 定 0..1 厚度的范围；
#:   * `_EmissiveColor`：自发光颜色因子（HDRP 与贴图相乘）。
COLOR_CARRY_ONLY: tuple[str, ...] = (
    "_IridescenceThicknessRemap",
    "_EmissiveColor",
    #: ★ 2026-10-17：涂装层的平铺（Vector4，XY 有用）—— 逐套皮肤不同（2.0 / 1.2 / 2.0）。
    "_CamoTiling",
)

PARAM_TO_NODE_INPUT: Mapping[str, tuple[str, bool]] = {
    # —— HDRP/Lit（M1）——
    "_Metallic": ("Metallic", False),
    "_Smoothness": ("Roughness", True),
    "_NormalScale": ("Normal Map.Strength", False),
    "_AlphaCutoff": ("Alpha（配合 blend_method=CLIP）", False),
    "_EmissiveIntensity": ("Emission Strength", False),
    "_CoatMask": ("Coat Weight", False),
    # —— UnitV3（M2/M4）的真名 ——
    #: ⛔ `_Metal` **故意不在这里**：它声明为 `Range(0, 2)`，与 Principled 的 Metallic(0..1)
    #: 不同域；而且 `blender_addon/materials.py::_NON_RENDERING_SCALARS` 明确写着
    #: M2 的金属度/光滑度**不驱动渲染**（W5 判决实验）。要让它进这张表，
    #: 必须同步把那边的 skip 名单从 `_Metallic` 改成 `_Metal`（blender_addon 范围内的跟进）。
    #: ⇒ 现状：`_Metal` 能被读进 plan、能被写回侧原样保留，
    #: 但**不碰 socket**（与改前一致，不是回归）。
    "_NormalStrange": ("Normal Map.Strength", False),
    "_AlphaClipThreshold": ("Alpha（配合 blend_method=CLIP）", False),
    "_EmissionStrange": ("Emission Strength", False),
    # —— Infantry（M3）的真名 ——
    "_Normal_Str": ("Normal Map.Strength", False),
    "_AlphaClipping": ("Alpha（配合 blend_method=CLIP）", False),
}
