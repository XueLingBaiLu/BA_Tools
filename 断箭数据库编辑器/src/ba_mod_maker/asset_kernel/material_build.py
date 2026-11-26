"""W10-② · Material 从零序列化：按真件类型树的**全部键**构造字段树 + 映射表逐条对拍。

口径出处：设计稿 §9.5 施工顺序 4（Mesh → Material → GameObject/Transform → …）、
§7.1 四张映射表、§7.2 节点树、§3.3 shader 分布；工单 W10 §3 判据 **N2**（材质逐条）
与 **N4**（依赖不悬空）。

## 为什么要有这个模块

W9 从零造的只有 Texture2D / Sprite / AssetBundle。Material 的字段树长得多
（真件类型树 **107 个节点 / 12 个顶层字段**），而且两条失败方式都是**静默**的：

* 字段树**缺**一个键 —— clone.write_object 直接抛（唯一的好消息）；
* 字段树**多**一个键 —— 被静默忽略，写出「少一段」的字节（游戏侧读到别的偏移）。

所以本模块把**顶层键集合**与**嵌套键路径集合**都做成常量尺子，并给
check_keyset / assert_keyset 两条闸；键集合的真值来自 units 包里 2,320 个真件
Material 的**全量扫描**（见下表），不是抄文档。

## 实测（units 包 · 2,320 个 Material · _work/w10_mat_deps.py + w10_mat_probe.json）

* 类型树：classID **21** / type_id **2** / 107 节点 / **12 个顶层字段**；
* 顶层键（顺序 = 类型树文档顺序，就是 MATERIAL_TOP_KEYS 那一串）：
  m_Name / m_Shader / m_ValidKeywords / m_InvalidKeywords / m_LightmapFlags /
  m_EnableInstancingVariants / m_DoubleSidedGI / m_CustomRenderQueue / stringTagMap /
  disabledShaderPasses / m_SavedProperties / m_BuildTextureStacks；
* roundtrip：读写往返**逐字节相同 2,320 / 2,320**（差 0）；
* PPtr 字段整棵树里只有 2 个：m_Shader（PPtr<Shader>）·
  m_SavedProperties.m_TexEnvs[].second.m_Texture（PPtr<Texture>）；
* pair 的形状：走 clone 的读侧就是 first / second 两个键（map/vector 的 data 是 pair）；
* ⚠ 本版**没有** m_ShaderKeywords —— 工单【要什么】点名了它，但真件类型树里
  **不存在**（2,320/2,320 个真件的键集合里都没有）；Unity 用
  m_ValidKeywords / m_InvalidKeywords 取代。多写它不会报错，只会被静默忽略 ——
  所以本模块**不写**它，并把它记进 ABSENT_FIELDS。

## 与 materials 的分工（⛔ 那边只读、不许改）

* materials 定「源 → Blender」的映射（M1~M4 表 + 节点树形状）；
* 本模块定「源 → **我们包里那个 Material**」的字段树，并提供**同一张映射表**下的
  逐条对拍（n2_rows）。写回方向（Blender → 游戏）由 W5/W6 的写回层负责。

## 边界

* 顶层零第三方依赖；不 import 宿主模块（asset_kernel 的 C5 契约）。
* **不猜**：认不出的 shader 取不到表（materials.table_for 返回 None）时，
  n2_rows 抛 AssetError，不回落、不近似。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ba_mod_maker.asset_kernel import materials
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "ABSENT_FIELDS",
    "MATERIAL_CLASS_ID",
    "MATERIAL_KEY_PATHS",
    "MATERIAL_TOP_KEYS",
    "MISSING_SLOT",
    "N2Row",
    "PAIR_FIRST",
    "PAIR_SECOND",
    "PptrRow",
    "assert_keyset",
    "check_keyset",
    "color_row",
    "dependency_rows",
    "float_row",
    "int_row",
    "key_paths_of",
    "material_fields",
    "material_fields_from_source",
    "n2_ok",
    "n2_rows",
    "n2_text",
    "pptr_rows",
    "shader_name_of",
    "shader_name_report",
    "slot_texture_ids",
    "string_tag_row",
    "summary_of",
    "tex_env",
    "tex_env_names",
    "unmapped_slots",
    "with_scalar",
    "with_texture",
]

#: Material 的 classID（Unity 的 ClassIDType.Material）。
MATERIAL_CLASS_ID = 21

#: map / vector 的元素是 pair：真件类型树里叫 first / second，
#: 所以字段树也必须是这两个键（改了就写不出字节）。
PAIR_FIRST = "first"
PAIR_SECOND = "second"

#: N2 的哨兵值：**槽本身不存在**。
#: 为什么要它：Unity 的「槽在但没赋图」是空 PPtr（读回来 m_PathID == 0 ⇒ None），
#: 而「我们把槽整个丢了」在 dict 里也是取不到 —— 两者不加区分的话，
#: 「丢一个未赋图的槽」这条负控会**假绿**（实测踩过：RU_T72B_track 的 _MaskMap 就没赋图）。
MISSING_SLOT = "<槽不存在>"

#: 真件 Material 类型树的**全部 12 个顶层字段**（顺序 = 类型树文档顺序）。
#: 判据：2,320/2,320 个真件的键集合与它**逐个相等**。
MATERIAL_TOP_KEYS: tuple[str, ...] = (
    "m_Name",
    "m_Shader",
    "m_ValidKeywords",
    "m_InvalidKeywords",
    "m_LightmapFlags",
    "m_EnableInstancingVariants",
    "m_DoubleSidedGI",
    "m_CustomRenderQueue",
    "stringTagMap",
    "disabledShaderPasses",
    "m_SavedProperties",
    "m_BuildTextureStacks",
)

#: 工单里点名、但**本版真件类型树里不存在**的字段（写进去只会被静默忽略）。
#: 留着这条是为了下一个人不再照着工单找它（找不到不是 bug）。
ABSENT_FIELDS: tuple[str, ...] = ("m_ShaderKeywords",)

#: 嵌套键路径的全集（数组用 [] 表示）。尺子要看的是「有没有这一个键」。
MATERIAL_KEY_PATHS: tuple[str, ...] = (
    "m_Name",
    "m_Shader",
    "m_Shader.m_FileID",
    "m_Shader.m_PathID",
    "m_ValidKeywords",
    "m_ValidKeywords[]",
    "m_InvalidKeywords",
    "m_InvalidKeywords[]",
    "m_LightmapFlags",
    "m_EnableInstancingVariants",
    "m_DoubleSidedGI",
    "m_CustomRenderQueue",
    "stringTagMap",
    "stringTagMap[]",
    "stringTagMap[].first",
    "stringTagMap[].second",
    "disabledShaderPasses",
    "disabledShaderPasses[]",
    "m_SavedProperties",
    "m_SavedProperties.m_TexEnvs",
    "m_SavedProperties.m_TexEnvs[]",
    "m_SavedProperties.m_TexEnvs[].first",
    "m_SavedProperties.m_TexEnvs[].second",
    "m_SavedProperties.m_TexEnvs[].second.m_Texture",
    "m_SavedProperties.m_TexEnvs[].second.m_Texture.m_FileID",
    "m_SavedProperties.m_TexEnvs[].second.m_Texture.m_PathID",
    "m_SavedProperties.m_TexEnvs[].second.m_Scale",
    "m_SavedProperties.m_TexEnvs[].second.m_Scale.x",
    "m_SavedProperties.m_TexEnvs[].second.m_Scale.y",
    "m_SavedProperties.m_TexEnvs[].second.m_Offset",
    "m_SavedProperties.m_TexEnvs[].second.m_Offset.x",
    "m_SavedProperties.m_TexEnvs[].second.m_Offset.y",
    "m_SavedProperties.m_Ints",
    "m_SavedProperties.m_Ints[]",
    "m_SavedProperties.m_Ints[].first",
    "m_SavedProperties.m_Ints[].second",
    "m_SavedProperties.m_Floats",
    "m_SavedProperties.m_Floats[]",
    "m_SavedProperties.m_Floats[].first",
    "m_SavedProperties.m_Floats[].second",
    "m_SavedProperties.m_Colors",
    "m_SavedProperties.m_Colors[]",
    "m_SavedProperties.m_Colors[].first",
    "m_SavedProperties.m_Colors[].second",
    "m_SavedProperties.m_Colors[].second.r",
    "m_SavedProperties.m_Colors[].second.g",
    "m_SavedProperties.m_Colors[].second.b",
    "m_SavedProperties.m_Colors[].second.a",
    "m_BuildTextureStacks",
    "m_BuildTextureStacks[]",
    "m_BuildTextureStacks[].groupName",
    "m_BuildTextureStacks[].itemName",
)

#: 数组**元素**的键路径（键路径尺子只对「父数组非空」要求这些）。
#: 判据：路径里出现过 [] 且自身不以 [] 结尾 = 它是某个元素的字段。
_ARRAY_ITEM_PATHS: frozenset[str] = frozenset(
    p for p in MATERIAL_KEY_PATHS if "[]" in p and not p.endswith("[]")
)


# --------------------------------------------------------------------------- #
# 从零构造：一行一行的零件（形状全部来自真件类型树）
# --------------------------------------------------------------------------- #
def pptr(path_id: int, file_id: int = 0) -> dict[str, int]:
    """PPtr 的字段树形状（12 B：i32 m_FileID + i64 m_PathID）。

    ⛔ 不 import clone 的 pptr：那个模块是「从模板克隆」的路子，本模块要**零模板**；
    形状只有 2 个键，重复一份不值当拉依赖。
    """
    return {"m_FileID": int(file_id), "m_PathID": int(path_id)}


def tex_env(
    slot: str,
    path_id: int,
    *,
    file_id: int = 0,
    scale: tuple[float, float] = (1.0, 1.0),
    offset: tuple[float, float] = (0.0, 0.0),
) -> dict[str, Any]:
    """m_TexEnvs 的一行：first = 槽名，second = {m_Texture, m_Scale, m_Offset}。"""
    return {
        PAIR_FIRST: str(slot),
        PAIR_SECOND: {
            "m_Texture": pptr(path_id, file_id),
            "m_Scale": {"x": float(scale[0]), "y": float(scale[1])},
            "m_Offset": {"x": float(offset[0]), "y": float(offset[1])},
        },
    }


def float_row(param: str, value: float) -> dict[str, Any]:
    return {PAIR_FIRST: str(param), PAIR_SECOND: float(value)}


def int_row(param: str, value: int) -> dict[str, Any]:
    return {PAIR_FIRST: str(param), PAIR_SECOND: int(value)}


def color_row(param: str, r: float, g: float, b: float, a: float) -> dict[str, Any]:
    """m_Colors 的一行（Unity 的 ColorRGBA 字段顺序是 r/g/b/a）。"""
    return {
        PAIR_FIRST: str(param),
        PAIR_SECOND: {"r": float(r), "g": float(g), "b": float(b), "a": float(a)},
    }


def string_tag_row(key: str, value: str) -> dict[str, str]:
    return {PAIR_FIRST: str(key), PAIR_SECOND: str(value)}


def material_fields(
    *,
    name: str,
    shader_path_id: int = 0,
    shader_file_id: int = 0,
    tex_envs: Sequence[Mapping[str, Any]] = (),
    floats: Sequence[Mapping[str, Any]] = (),
    colors: Sequence[Mapping[str, Any]] = (),
    ints: Sequence[Mapping[str, Any]] = (),
    valid_keywords: Sequence[str] = (),
    invalid_keywords: Sequence[str] = (),
    lightmap_flags: int = 0,
    enable_instancing_variants: bool = False,
    double_sided_gi: bool = False,
    custom_render_queue: int = -1,
    string_tag_map: Sequence[Mapping[str, str]] = (),
    disabled_shader_passes: Sequence[str] = (),
    build_texture_stacks: Sequence[Mapping[str, str]] = (),
) -> dict[str, Any]:
    """**从零**造一个 Material 的字段树（只依赖入参，不读任何模板对象）。

    返回的 dict 的键集合**恰好**等于 MATERIAL_TOP_KEYS（多一个少一个都不行，
    见 check_keyset）。行顺序就是入参顺序 —— 写出的字节里数组顺序 = 这里给的顺序，
    所以「照源序传」才能做逐字节往返。

    ⛔ 默认值不是从真件统计出来的，是 Unity 的「空材质」口径：
    m_CustomRenderQueue=-1（用 shader 的默认队列）、m_LightmapFlags=0、
    stringTagMap=[]、m_BuildTextureStacks=[]。真件里这些字段的值由调用方
    从源材质带过来（material_fields_from_source 就是这么做的）。
    """
    return {
        "m_Name": str(name),
        "m_Shader": pptr(shader_path_id, shader_file_id),
        "m_ValidKeywords": [str(k) for k in valid_keywords],
        "m_InvalidKeywords": [str(k) for k in invalid_keywords],
        "m_LightmapFlags": int(lightmap_flags),
        "m_EnableInstancingVariants": bool(enable_instancing_variants),
        "m_DoubleSidedGI": bool(double_sided_gi),
        "m_CustomRenderQueue": int(custom_render_queue),
        "stringTagMap": [dict(row) for row in string_tag_map],
        "disabledShaderPasses": [str(p) for p in disabled_shader_passes],
        "m_SavedProperties": {
            "m_TexEnvs": [dict(row) for row in tex_envs],
            "m_Ints": [dict(row) for row in ints],
            "m_Floats": [dict(row) for row in floats],
            "m_Colors": [dict(row) for row in colors],
        },
        "m_BuildTextureStacks": [dict(row) for row in build_texture_stacks],
    }


# --------------------------------------------------------------------------- #
# 键集合尺子
# --------------------------------------------------------------------------- #
def key_paths_of(value: Any, prefix: str = "", out: dict[str, Any] | None = None) -> dict[str, Any]:
    """遍历字段树，收集**所有键路径**（数组元素用 [] 归一）。

    与真件类型树打出来的那一份（MATERIAL_KEY_PATHS）比 —— 这是「多一个键 /
    少一个键」的机械判据；只比顶层键不够（嵌套少一个键同样是坏字节）。
    """
    if out is None:
        out = {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.setdefault(path, type(item).__name__)
            key_paths_of(item, path, out)
    elif isinstance(value, (list, tuple)):
        # ⚠ 数组的 [] 格里存**元素个数**（不是类型名）：空数组照样写 i32 count=0，
        # 所以 [] 这个键必须在；而它的元素键（）只有在非空时才存在 ——
        # check_keyset 靠这个数字区分「空数组」与「少写了元素键」。
        out.setdefault(f"{prefix}[]", len(value))
        for item in value:
            if isinstance(item, (Mapping, list, tuple)):
                key_paths_of(item, f"{prefix}[]", out)
    return out


def check_keyset(
    fields: Mapping[str, Any],
    *,
    top_keys: Sequence[str] = MATERIAL_TOP_KEYS,
    key_paths: Sequence[str] = MATERIAL_KEY_PATHS,
) -> tuple[str, ...]:
    """返回违规清单（空 = 过）。三件事：顶层多/少、嵌套多/少、数组元素键。

    * **少一个键** ⇒ 违规（写出去会抛，或者少一段字节）；
    * **多一个键** ⇒ 违规（会被静默忽略 —— 这是最危险的那种「看着对」）；
    * 空数组的 xxx[].yyy 不要求出现（真件空数组也只写一个 i32 count=0）。
    """
    bad: list[str] = []
    got_top = list(fields.keys())
    for key in top_keys:
        if key not in got_top:
            bad.append(f"缺顶层键 {key}")
    for key in got_top:
        if key not in top_keys:
            bad.append(f"多顶层键 {key}")
    if got_top != list(top_keys) and sorted(got_top) == sorted(top_keys):
        bad.append(f"顶层键顺序与真件不同：{got_top}")

    actual_map = key_paths_of(fields)
    actual = set(actual_map)
    expected = set(key_paths)
    for path in sorted(expected - actual):
        if path in _ARRAY_ITEM_PATHS:
            parent = path.split("[]")[0] + "[]"
            if actual_map.get(parent) == 0:
                continue  # 空数组：元素键本来就不存在
        bad.append(f"缺键路径 {path}")
    for path in sorted(actual - expected):
        bad.append(f"多键路径 {path}")
    return tuple(bad)


def assert_keyset(fields: Mapping[str, Any], **kwargs: Any) -> None:
    """check_keyset 的抛错版（写字节**之前**过它）。"""
    bad = check_keyset(fields, **kwargs)
    if bad:
        raise AssetError(
            f"Material 字段树的键集合与真件不符（{len(bad)} 条）",
            hint="; ".join(bad[:8]),
        )


# --------------------------------------------------------------------------- #
# 读出来的东西 → 素（materials.plan_material 认的输入）
# --------------------------------------------------------------------------- #
def _saved(fields: Mapping[str, Any]) -> Mapping[str, Any]:
    saved = fields.get("m_SavedProperties")
    if not isinstance(saved, Mapping):
        raise AssetError("Material 字段树里没有 m_SavedProperties（形状不对）")
    return saved


def _pairs(saved: Mapping[str, Any], key: str) -> tuple[tuple[str, Any], ...]:
    rows = saved.get(key) or ()
    return tuple((str(row.get(PAIR_FIRST)), row.get(PAIR_SECOND)) for row in rows)


def tex_env_names(fields: Mapping[str, Any]) -> tuple[str, ...]:
    """m_TexEnvs 的槽名（**全部**，含没赋图的）。"""
    return tuple(str(row.get(PAIR_FIRST)) for row in _saved(fields).get("m_TexEnvs") or ())


def slot_texture_ids(fields: Mapping[str, Any]) -> dict[str, int | None]:
    """槽名 → 贴图 pathID（没赋图 / 空引用 ⇒ None）。"""
    out: dict[str, int | None] = {}
    for row in _saved(fields).get("m_TexEnvs") or ():
        tex = (row.get(PAIR_SECOND) or {}).get("m_Texture") or {}
        path_id = int(tex.get("m_PathID", 0) or 0)
        out[str(row.get(PAIR_FIRST))] = path_id or None
    return out


def summary_of(fields: Mapping[str, Any], *, shader: str = "") -> dict[str, Any]:
    """字段树 → materials.plan_material 认的那份素。

    ⚠ shader 必须由调用方给（材质对象里只有 PPtr<Shader>，**名字在 Shader 对象里**）——
    本模块不查包、不猜名字。
    """
    saved = _saved(fields)
    return {
        "name": str(fields.get("m_Name") or ""),
        "shader": str(shader),
        # ⚠ 空引用（m_PathID == 0）必须报成 **None**：materials._slots_of 用
        # 「tid is not None」判「这个槽赋了图」。给 0 的话，真件里那几十个空槽
        # 会被算成「已赋但没映射」⇒ 还原度从 完整 掉成 部分（实测踩过：
        # RU_T72B_track 该是 完整，给 0 就变 部分，与 W4 交接的读数对不上）。
        "texture_slots": [
            (
                str(row.get(PAIR_FIRST)),
                int((row.get(PAIR_SECOND) or {}).get("m_Texture", {}).get("m_PathID", 0) or 0)
                or None,
            )
            for row in saved.get("m_TexEnvs") or ()
        ],
        "floats": {name: value for name, value in _pairs(saved, "m_Floats")},
        "colors": {name: value for name, value in _pairs(saved, "m_Colors")},
        "ints": {name: value for name, value in _pairs(saved, "m_Ints")},
        "valid_keywords": [str(k) for k in fields.get("m_ValidKeywords") or ()],
        "invalid_keywords": [str(k) for k in fields.get("m_InvalidKeywords") or ()],
        "keywords": list(
            dict.fromkeys(
                [str(k) for k in fields.get("m_ValidKeywords") or ()]
                + [str(k) for k in fields.get("m_InvalidKeywords") or ()]
                + [str(p) for p in fields.get("disabledShaderPasses") or ()]
            )
        ),
    }


# --------------------------------------------------------------------------- #
# shader 名字：判据字段是 m_ParsedForm.m_Name，不是 m_Name
# --------------------------------------------------------------------------- #
def shader_name_of(shader_fields: Mapping[str, Any]) -> str:
    """Shader 对象字段树 → **真名**。

    实测口径（设计稿 §3.3 的坑）：m_Name 是**空串**，真名在 m_ParsedForm.m_Name。
    取不到就返回空串（调用方按「认不出 shader」处理，**不猜**）。
    """
    parsed = shader_fields.get("m_ParsedForm")
    if isinstance(parsed, Mapping) and parsed.get("m_Name"):
        return str(parsed["m_Name"])
    return str(shader_fields.get("m_Name") or "")


def shader_name_report(shader_fields: Mapping[str, Any]) -> dict[str, Any]:
    """给判据一行用的读数：两个字段各是什么 + 用的是哪一个。"""
    parsed = shader_fields.get("m_ParsedForm")
    parsed_name = str(parsed.get("m_Name") or "") if isinstance(parsed, Mapping) else ""
    plain = str(shader_fields.get("m_Name") or "")
    return {
        "m_Name": plain,
        "m_ParsedForm.m_Name": parsed_name,
        "used": parsed_name or plain,
        "source": "m_ParsedForm.m_Name" if parsed_name else ("m_Name" if plain else "—"),
    }


# --------------------------------------------------------------------------- #
# 从源材质构造我们的材质（走映射表，不整份照抄）
# --------------------------------------------------------------------------- #
def _table_for_source(
    source: Mapping[str, Any],
    table: materials.MaterialTable | None,
    shader: str,
) -> materials.MaterialTable:
    if table is not None:
        return table
    resolved = materials.table_for(shader)
    if resolved is None:
        raise AssetError(
            f"没有 {shader!r} 的映射表（不猜）",
            hint="先取 Shader 对象的 m_ParsedForm.m_Name，再走 materials.table_for()",
        )
    return resolved


def _mapped_slots(ids: Mapping[str, int | None], table: materials.MaterialTable) -> set[str]:
    return {slot for slot in ids if table.matches(slot)}


def unmapped_slots(source: Mapping[str, Any], table: materials.MaterialTable) -> tuple[str, ...]:
    """源里**已赋图**、但映射表不认的槽（N4：这些槽不能带进我们包，否则贴图悬空）。"""
    ids = slot_texture_ids(source)
    return tuple(slot for slot, tid in ids.items() if tid is not None and not table.matches(slot))


def material_fields_from_source(
    source: Mapping[str, Any],
    *,
    table: materials.MaterialTable | None = None,
    shader: str = "",
    texture_path_ids: Mapping[str, int] | None = None,
    name: str | None = None,
    shader_path_id: int | None = None,
    shader_file_id: int = 0,
) -> dict[str, Any]:
    """源材质字段树 → **我们包里**那个材质的字段树（按 M1~M4 表逐条搬）。

    搬什么（顺序 = 源的顺序，为的是能对拍与逐字节往返）：

    * m_TexEnvs：**只保留映射表认得的槽**（materials.MaterialTable.matches），
      槽名**原样**；贴图 pathID 换成 texture_path_ids[槽名]（不给就沿用源值，
      用于「身份往返」的正控）；
    * m_Floats / m_Colors / m_Ints：只保留表里点名的标量（table.scalars），
      值**原样**（Blender 侧的反演在 materials.ScalarBinding.apply）；
    * m_ValidKeywords / m_InvalidKeywords / m_LightmapFlags / m_CustomRenderQueue /
      stringTagMap / disabledShaderPasses / m_BuildTextureStacks：**原样照搬**
      （它们决定 shader 变体与实际渲染，不是 Blender 侧的映射对象；
      丢一个就是「看着对、画出来不一样」）；
    * m_EnableInstancingVariants / m_DoubleSidedGI：原样照搬（真值来自源）。

    ⛔ 不照搬**未映射槽**（理由：那些贴图在我们包里没有对应对象，照搬 = 悬空 PPtr）。

    ## 保留清单（2026-10-07 加，审计 N1 的**第一步**）

    `table.scalars` 里现在有一条**不驱动渲染、只要求别丢**的：
    `_EnableBlendModePreserveSpecularLighting`（11,902 个真材质里 8734 个 = 1）。
    它进这张表 ⇒ 写侧不再把它丢掉。**这不等于还原了它**：HDRP 里该 uniform=1 时
    specular 乘 1.0（`Material.hlsl:58-69`），而 Blender 的 alpha 混合仍会把镜面乘 alpha ——
    补偿是另一件事，本版没做。
    ⚠ 其余"shader 真声明过的浮点"（`_BlendMode` / `_SurfaceType` / 各 pass 状态…）**仍然会丢**：
    全量泛化要先把"包里的字段集合"这条契约重新定死（它会change 逐字节往返的判据），
    按 Lead 裁定**留到验证完最小改动之后**。

    ## 这条写侧今天从哪条路走得通（Lead 2026-10-07 的口径）

    `material_fields_from_source` **没有 GUI / CLI 入口** —— 对已发布产品（编辑→校验→打包）
    **够不着**。能走到它的只有三条：① `unit_bundle.assemble_unit_bundle` 那条"从零造单位模型包"
    （route A；入口是 `_work/w10_lead_build.py` 这类脚本）；② `blender_addon/criteria.py` 的 A4 判据
    （在 Blender 里跑）；③ `tests/test_build_material_from_scratch.py` 与
    `tests/test_build_unit_bundle.py`。
    ⇒ 按 bug 修，但**别以为产品主流程在用**。
    """
    tbl = _table_for_source(source, table, shader)
    ids = slot_texture_ids(source)
    mapped = _mapped_slots(ids, tbl)
    tex_envs = []
    for row in _saved(source).get("m_TexEnvs") or ():
        slot = str(row.get(PAIR_FIRST))
        if slot not in mapped:
            continue
        second = dict(row.get(PAIR_SECOND) or {})
        tex = dict(second.get("m_Texture") or {})
        src_pid = int(tex.get("m_PathID", 0) or 0)
        if texture_path_ids is not None and slot in texture_path_ids:
            tex["m_PathID"] = int(texture_path_ids[slot])
        elif src_pid:
            tex["m_PathID"] = src_pid
        second["m_Texture"] = tex
        tex_envs.append({PAIR_FIRST: slot, PAIR_SECOND: second})

    scalar_params = {binding.param for binding in tbl.scalars}
    floats = [
        dict(row)
        for row in _saved(source).get("m_Floats") or ()
        if str(row.get(PAIR_FIRST)) in scalar_params
    ]
    colors = [
        dict(row)
        for row in _saved(source).get("m_Colors") or ()
        if str(row.get(PAIR_FIRST)) in scalar_params
    ]
    ints = [
        dict(row)
        for row in _saved(source).get("m_Ints") or ()
        if str(row.get(PAIR_FIRST)) in scalar_params
    ]

    src_shader = source.get("m_Shader") or {}
    return material_fields(
        name=str(name if name is not None else source.get("m_Name") or ""),
        shader_path_id=int(
            shader_path_id if shader_path_id is not None else src_shader.get("m_PathID", 0)
        ),
        shader_file_id=int(
            shader_file_id if shader_path_id is not None else src_shader.get("m_FileID", 0)
        ),
        tex_envs=tex_envs,
        floats=floats,
        colors=colors,
        ints=ints,
        valid_keywords=[str(k) for k in source.get("m_ValidKeywords") or ()],
        invalid_keywords=[str(k) for k in source.get("m_InvalidKeywords") or ()],
        lightmap_flags=int(source.get("m_LightmapFlags") or 0),
        enable_instancing_variants=bool(source.get("m_EnableInstancingVariants")),
        double_sided_gi=bool(source.get("m_DoubleSidedGI")),
        custom_render_queue=int(source.get("m_CustomRenderQueue") or 0),
        string_tag_map=[dict(row) for row in source.get("stringTagMap") or ()],
        disabled_shader_passes=[str(p) for p in source.get("disabledShaderPasses") or ()],
        build_texture_stacks=[dict(row) for row in source.get("m_BuildTextureStacks") or ()],
    )


# --------------------------------------------------------------------------- #
# N2：逐条对拍
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class N2Row:
    """N2 的一行读数：源值 → 我们包里的值。

    源 / 包里是**包里**的值；note 里带**Blender 侧**该拿到的值（例：Roughness =
    1 − _Smoothness、MaskMap 的 A 通道）。两者都摆出来，是因为「包里的值一致」与
    「Blender 侧拿到的一致」是两条不同的判据。
    """

    kind: str
    key: str
    source: Any
    built: Any
    mapped_to: str = ""
    role: str = ""
    channel: str = ""
    invert: bool = False
    note: str = ""
    #: True = 「丢掉是规定动作」的那一行（映射表不认的槽）。
    #: N2 的口径是**按映射表逐条**：表不认的槽本来就不该带进本包（N4：会悬空），
    #: 所以它的 source/built 不相等**不算** N2 不一致；但不许静默 ——
    #: 行照摆，line() 打 DROP，strict_mismatch 仍然是 True。
    expected_drop: bool = False

    @property
    def strict_mismatch(self) -> bool:
        """不做任何豁免的比较（判据想看「到底哪里不一样」时用它）。"""
        return self.source != self.built

    @property
    def match(self) -> bool:
        return True if self.expected_drop else self.source == self.built

    def line(self) -> str:
        flag = (
            "DROP"
            if (self.expected_drop and self.strict_mismatch)
            else ("OK " if self.match else "BAD")
        )
        role = (
            f" [{self.role}" + (f"/{self.channel}" if self.channel else "") + "]"
            if self.role
            else ""
        )
        tonext = f" → {self.mapped_to}" if self.mapped_to else ""
        inv = " (invert)" if self.invert else ""
        return (
            f"{flag} {self.kind:<8} {self.key}{role}{tonext}{inv}: "
            f"源={self.source!r} 包里={self.built!r}" + (f"  # {self.note}" if self.note else "")
        )


def n2_rows(
    source: Mapping[str, Any],
    built: Mapping[str, Any],
    *,
    table: materials.MaterialTable | None = None,
    shader: str = "",
    include_links: bool = True,
) -> tuple[N2Row, ...]:
    """把「源材质」与「我们包里的材质」**逐条**摆开（N2 的尺子）。

    覆盖面（工单【验收判据】(a) 点名的每一条都在，且有专门的角色标注）：

    1. **槽位**：源里每一个 m_TexEnvs 槽 → 同名的我们那一格（pathID 必须相等）；
       我们这边多出来的槽也算一行（built 那侧多 = 违规）；
    2. **标量**：表里点名的每一个参数（_Metallic / _Smoothness / _NormalScale /
       _AlphaCutoff / _EmissiveIntensity / _CoatMask / _SurfaceType…）→ 同名的浮点；
       note 里给 ScalarBinding.apply 之后的 Blender 侧值（含 invert）；
    3. **角色连线**：materials.NODE_SHAPE 的每一条（Base Color / Normal / Mask R→Metallic /
       Mask A→Roughness / Mask G→AO 忽略 / Emission）—— 由映射表把槽解析成角色后取贴图；
    4. **关键字**：m_ValidKeywords / m_InvalidKeywords **有序 + 集合**都要一致；
    5. **alpha 模式**：materials.plan_material 的 alpha_mode 相等（CLIP/BLEND/OPAQUE）。

    table 不给就按 shader 查 materials.table_for；查不到 ⇒ 抛（不猜）。
    """
    tbl = _table_for_source(source, table, shader)
    rows: list[N2Row] = []

    src_ids = slot_texture_ids(source)
    our_ids = slot_texture_ids(built)
    for slot, src_pid in src_ids.items():
        role = tbl.role_of(slot) or ""
        # ⚠「槽在但没赋图」(None) 与「槽被丢了」必须分开：后者记 MISSING_SLOT，
        # 否则丢一个未赋图的槽（真件里很常见）会让 N2 假绿。
        built_pid = our_ids.get(slot, MISSING_SLOT)
        # ⚠ 关键口径：**未赋图**且表不认 ⇒ 丢弃是规定动作（expected_drop）；
        # **已赋图**而表不认 ⇒ 正是「装错表 / 表缺一条」的现场，必须红
        # （否则拿 M3 去解 M1 的履带会**假绿** —— 实测踩过）。
        drop_ok = not role and src_pid is None
        rows.append(
            N2Row(
                kind="slot" if role else "unmapped",
                key=slot,
                source=src_pid,
                built=built_pid,
                role=role,
                expected_drop=drop_ok,
                note=(
                    ""
                    if role
                    else (
                        "映射表不认这个槽，且**没赋图** ⇒ 按规定丢弃"
                        if drop_ok
                        else "映射表不认这个槽，但**源里赋了图** ⇒ 不许静默丢（N4：贴图会悬空）"
                    )
                ),
            )
        )
    for slot, our_pid in our_ids.items():
        if slot not in src_ids:
            rows.append(
                N2Row(
                    kind="slot",
                    key=slot,
                    source=MISSING_SLOT,
                    built=our_pid,
                    role=tbl.role_of(slot) or "",
                    note="我们包里多出来的槽",
                )
            )

    src_ptrs = dict(_pairs(_saved(source), "m_Floats"))
    our_ptrs = dict(_pairs(_saved(built), "m_Floats"))
    for binding in tbl.scalars:
        if binding.param not in src_ptrs:
            continue
        value = src_ptrs[binding.param]
        rows.append(
            N2Row(
                kind="scalar",
                key=binding.param,
                source=value,
                built=our_ptrs.get(binding.param),
                mapped_to=binding.blender_input,
                invert=binding.invert,
                note=(
                    f"Blender 侧={binding.apply(float(value))!r}"
                    + (f"（{binding.note}）" if binding.note else "")
                ),
            )
        )

    if include_links:
        plan = materials.plan_material(summary_of(source, shader=tbl.shader), table=tbl)
        ours = materials.plan_material(summary_of(built, shader=tbl.shader), table=tbl)
        src_slots = {s.role: s for s in plan.slots}
        our_slots = {s.role: s for s in ours.slots}
        for link in materials.NODE_SHAPE:
            src_slot = src_slots.get(link.role)
            our_slot = our_slots.get(link.role)
            rows.append(
                N2Row(
                    kind="link",
                    key=f"{link.role}" + (f"/{link.channel}" if link.channel else ""),
                    source=src_slot.texture_id if src_slot else None,
                    built=our_slot.texture_id if our_slot else None,
                    mapped_to=link.blender_input,
                    role=link.role,
                    channel=link.channel or "",
                    invert=link.invert,
                    note="标注忽略（Principled 没有这个输入）" if link.ignored else link.note,
                )
            )

    for key in ("m_ValidKeywords", "m_InvalidKeywords"):
        rows.append(
            N2Row(
                kind="keyword",
                key=key,
                source=[str(k) for k in source.get(key) or ()],
                built=[str(k) for k in built.get(key) or ()],
                note="有序 + 集合都要一致（shader 变体按它选）",
            )
        )
    rows.append(
        N2Row(
            kind="alpha",
            key="alpha_mode",
            source=materials.plan_material(
                summary_of(source, shader=tbl.shader), table=tbl
            ).alpha_mode,
            built=materials.plan_material(
                summary_of(built, shader=tbl.shader), table=tbl
            ).alpha_mode,
            note="_ALPHATEST_ON ⇒ CLIP；_SurfaceType ≥ 0.5 ⇒ BLEND",
        )
    )
    return tuple(rows)


def n2_ok(rows: Iterable[N2Row]) -> bool:
    return all(row.match for row in rows)


def n2_text(rows: Iterable[N2Row]) -> str:
    return "\n".join(row.line() for row in rows)


def with_scalar(fields: Mapping[str, Any], param: str, value: float) -> dict[str, Any]:
    """复制一份、把某个浮点改掉（**负控专用**：改完 N2 必须红）。"""
    out = _deep_copy(fields)
    floats = out["m_SavedProperties"]["m_Floats"]
    for row in floats:
        if str(row.get(PAIR_FIRST)) == param:
            row[PAIR_SECOND] = float(value)
            return out
    out["m_SavedProperties"]["m_Floats"].append(float_row(param, value))
    return out


def with_texture(fields: Mapping[str, Any], slot: str, path_id: int) -> dict[str, Any]:
    """复制一份、把某个槽的贴图 pathID 改掉（负控专用）。"""
    out = _deep_copy(fields)
    for row in out["m_SavedProperties"]["m_TexEnvs"]:
        if str(row.get(PAIR_FIRST)) == slot:
            row[PAIR_SECOND]["m_Texture"]["m_PathID"] = int(path_id)
            return out
    raise AssetError(f"源材质里没有槽 {slot!r}")


def _deep_copy(fields: Mapping[str, Any]) -> dict[str, Any]:
    import copy  # noqa: PLC0415 -- 只有负控助手用它，别让顶层多一个入口

    return copy.deepcopy(dict(fields))


# --------------------------------------------------------------------------- #
# N4：依赖清单（材质引用的外部资产）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class PptrRow:
    """材质字段树里的一个 PPtr（N4 依赖清单的一行）。"""

    field_path: str
    kind: str
    slot: str
    file_id: int
    path_id: int

    @property
    def null(self) -> bool:
        return self.file_id == 0 and self.path_id == 0

    @property
    def external(self) -> bool:
        return self.file_id != 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "field_path": self.field_path,
            "kind": self.kind,
            "slot": self.slot,
            "file_id": self.file_id,
            "path_id": self.path_id,
            "null": self.null,
            "external": self.external,
        }


def pptr_rows(fields: Mapping[str, Any]) -> tuple[PptrRow, ...]:
    """材质里的**全部** PPtr：1 个 m_Shader + 每个 m_TexEnvs 槽 1 个。

    ⛔ 别用「扫字节找 12 B 结构」那种启发式：真件类型树整棵树里**只有这 2 个 PPtr
    字段**（实测），按字段走就够，而且不会把浮点误判成 pathID。
    """
    out: list[PptrRow] = []
    shader = fields.get("m_Shader") or {}
    out.append(
        PptrRow(
            field_path="m_Shader",
            kind="shader",
            slot="",
            file_id=int(shader.get("m_FileID", 0) or 0),
            path_id=int(shader.get("m_PathID", 0) or 0),
        )
    )
    for row in _saved(fields).get("m_TexEnvs") or ():
        tex = (row.get(PAIR_SECOND) or {}).get("m_Texture") or {}
        out.append(
            PptrRow(
                field_path=f"m_SavedProperties.m_TexEnvs[{row.get(PAIR_FIRST)}].m_Texture",
                kind="texture",
                slot=str(row.get(PAIR_FIRST)),
                file_id=int(tex.get("m_FileID", 0) or 0),
                path_id=int(tex.get("m_PathID", 0) or 0),
            )
        )
    return tuple(out)


def dependency_rows(
    fields: Mapping[str, Any],
    *,
    shader: str = "",
    table: materials.MaterialTable | None = None,
    texture_names: Mapping[int, str] | None = None,
) -> tuple[dict[str, Any], ...]:
    """N4 依赖清单的原料行（外部资产：shader PPtr + 每个贴图 PPtr）。

    每行给：字段路径 / 类型 / 槽名 / pathID / 是不是跨文件 / 映射表认不认这个槽，
    以及一句搬得动搬不动的**理由**。
    """
    names = texture_names or {}
    rows = []
    for row in pptr_rows(fields):
        item = row.as_dict()
        item["shader_name"] = shader if row.kind == "shader" else ""
        item["texture_name"] = names.get(row.path_id, "") if row.kind == "texture" else ""
        item["table_role"] = (
            table.role_of(row.slot) if table and row.kind == "texture" else ""
        ) or ""
        item["table_known"] = bool(table and row.kind == "texture" and table.matches(row.slot))
        if row.null:
            item["movable"] = "n/a"
            item["reason"] = "空引用（Unity 的 null），不需要搬"
        elif row.external:
            item["movable"] = "no"
            item["reason"] = (
                f"跨文件引用（m_FileID={row.file_id}）—— 本包不声明 bundle 依赖，"
                "搬进来之前必须先把 fileID 改成 0 并给对象一个新 pathID"
            )
        else:
            item["movable"] = "yes"
            item["reason"] = (
                "包内 PPtr：把对象一起搬进本包（pathID 重映射）即可，"
                "本包不声明依赖（设计稿 §9.5 步骤 5）"
            )
        rows.append(item)
    return tuple(rows)
