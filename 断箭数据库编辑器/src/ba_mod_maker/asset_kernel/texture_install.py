r"""texture_install —— 把**一张 PNG 的像素**带进一个既有的件包（等长 / 变长都收）。

## 它做的是哪一件事（与 `skin_install` 的分工，⛔ 两份逻辑各自一份）

| 模块 | 做什么 | 改什么 |
|---|---|---|
| `skin_install` | 槽指到**包内已有的另一张图** | 一个 PPtr（8 字节） |
| **`texture_install`** | **把我们的字节带进包** | PNG → BC3 ＋ 满 mip → 本包 `.resS` → 槽指到它 |

## 六条口径

1. ★ **尺寸 / 格式 / mip 变了也收**（2026-10-11 变长档）：PNG 的宽 / 高 / mip 数**允许与受害者不同**
   —— 这时贴图那**六个数一起落位**：`m_Width` / `m_Height` / `m_MipCount` / `m_TextureFormat` /
   `m_CompleteImageSize` ＋ `m_StreamData{path,offset,size}`；新像素照旧只进本包 `.resS`。
   ⛔ 工单 §7 坑 2：**只改 `m_TextureFormat`、把长度留旧值 = 必坏**（Unity 会按新格式去解一段
   旧长度的字节）⇒ 落盘之后**必须**过 `verify_texture_out` 那把尺子（出包读回，六读数逐条对）。
   ⛔ 不新增对象、⛔ 不动层级、⛔ **不缩放**（PNG 是几乘几就写几乘几）。
   mip 数：不给 `mips` 时 —— 尺寸与受害者相同就**照抄受害者的**（等长档因此逐字节不变），
   尺寸不同就用**满链**（逐级编到 1x1）。
2. **只读源包、只写 `--out`**：⛔ 不碰游戏目录（装机归 `newasset add-model`，那是另一条命令）。
3. ★ **alpha 门控长在这里**（2026-10-10 真机兑现过：斜坡 alpha 图 ⇒ 车身 **50.20%** 的面被裁成洞）：
   * 目标材质 `_ENABLEALPHACLIPPING == 1` 且图里 `alpha < _AlphaCutoff*255` ⇒
     **默认自动把 alpha 补成全 255**，并印一行大字说清"为什么动了你的图"；
   * 同一情形给了 `alpha="keep"` ⇒ **当场 `AssetError` 拒绝**（fail-closed，hint 带百分比）；
   * `_ENABLEALPHACLIPPING != 1` ⇒ **不动 alpha**（原样编码），两个读数照样印出来。
4. **源包里的对象一个字节都不许换形状**：除被改的那几个对象外，其余对象**原样搬字节**
   （`ObjectSpec(payload=…)`），所以"逐对象对拍"能证明没碰结构。
5. ★ **写完要读回**（2026-10-11 加，`§7 坑 2` 的另一半）：`apply_texture` 落盘之后**再开一遍出包**，
   把六个读数与 `.resS` 节点 size、以及 `.resS` 里那一段像素逐条对（`verify_texture_out`）——
   任何一条对不上**当场抛**，⛔ 不许拿"看着像对上了"当结论。
6. ★ **A5 自足化**（2026-10-11 加，本模块的第二件事）：**闭包里其它流式贴图的纹素也从源包 .resS
   原样搬进本包 .resS**（逐段追加，每段起点按 `STREAM_APPEND_ALIGN = 16` 对齐），并把它们的
   `m_StreamData.path/offset` 指到本包那个节点 —— 否则新包里那些贴图指的是**一个不存在的节点**
   （引擎对丢纹素是**静默**的 ⇒ 装机后是白图，就是 A5 那条红，判据在
   `unit_bundle.verify_unit_bundle` 的 A5 一段）。
   ⛔ **原样搬字节**：不重编码、不动 `m_TextureFormat` / `m_MipCount` / `m_CompleteImageSize`。
   ⛔ 搬不动的（源包里**也没有**那段字节 / 指着别的包 / 路径是空的）**如实报出来**
   （`stream_stuck`），一条都不许静默算绿。
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import builder, clone, skin_install, textures
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.asset_kernel.container_addr import resolve_container_path
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "ALPHA_MODES",
    "CLASS_ASSETBUNDLE",
    "CLASS_MATERIAL",
    "CLASS_TEXTURE2D",
    "CLASS_TRANSFORM",
    "DXT5",
    "GATE_KEY",
    "STREAM_APPEND_ALIGN",
    "AlphaNote",
    "AppliedEdit",
    "ForeignStream",
    "RgbaImage",
    "StreamMove",
    "StreamStuck",
    "TextureApplyPlan",
    "TextureReading",
    "alpha_note",
    "apply_texture",
    "encode_payload",
    "find_material_by_name",
    "foreign_streams",
    "full_mip_count",
    "material_alpha_gate",
    "plan_texture_apply",
    "read_png",
    "read_stream_textures",
    "read_texture_back",
    "replace_alpha",
    "texture_readback_problems",
    "verify_streams_out",
    "verify_texture_out",
]

#: classID（真件实测）。
CLASS_TEXTURE2D = 28
CLASS_MATERIAL = 21
CLASS_ASSETBUNDLE = 142
CLASS_TRANSFORM = 4
CLASS_MESH_RENDERER = 23
CLASS_SKINNED_MESH_RENDERER = 137
#: 渲染器：找"谁在用这颗材质"用（材质挂在渲染器上，渲染器再往上走到 prefab 根）。
RENDERER_CLASSES = (CLASS_MESH_RENDERER, CLASS_SKINNED_MESH_RENDERER)

#: 我们的字节一律编成 DXT5 / BC3（真件里 2048²·12 级就等于 5,592,432 B）。
DXT5 = 12
#: alpha 口径：默认补不透明；keep = 图什么样就什么样（门控开着时会被拒）。
ALPHA_MODES = ("opaque", "keep")

#: 材质上那两个 alpha 门控字段（HDRP/Lit 与 UnitV3/UnitV4 叫法不同，两个都读）。
#: 颜色层：由 **shader 自己写的描述**认（`m_PropInfo.m_Props[].m_Description`），
#: ⛔ **不写死槽名** —— 槽名里那串 hash（`Layer_A97CDC25`）会随 shader 变，desc 才是 shader 的契约。
#: 实测（2026-10-10 · UnitV4_Camo）：`Layer_A97CDC25` = 'Albedo' · `Layer_A97CDC25_1` = 'Camo'；
#: UnitV3（BALT 那件）只有 'Albedo' 一个 ⇒ 那种材质**不该**有"还有别的颜色层"这条警告。
COLOR_LAYER_DESCS = ("Albedo", "Camo")
#: 默认拿哪个颜色层的贴图当受害者（认不出 'Albedo' 就退回第一个颜色层）。
PRIMARY_DESC = "Albedo"
#: 只指了一个槽、而材质还有别的颜色层时印的那条（话术 Lead 定，⛔ 别改口径）。
WARN_OTHER_COLOR_LAYER = (
    "⚠ 只指了 {slot}，而该材质还有一个颜色层 {other}（shader 自写 desc='{desc}'）没换 ⇒ "
    "**车身会被游戏原涂装以约 24% 权重调制**（实测读数；diffuse 那一半没测到，权重可能更大）。"
    "要避免就**不指定 --slot**（默认全指）。"
)
GATE_KEY = "_ENABLEALPHACLIPPING"
CUTOFF_KEYS = ("_AlphaCutoff", "_AlphaClipThreshold")
#: m_StreamData 的路径前缀（真件口径）。
ARCHIVE_PREFIX = "archive:/"
#: 往 .resS **追加**时每段的起点对齐（真件实测 200/200 都是 16 的倍数，见交接 §4 第 4 条）。
STREAM_APPEND_ALIGN = 16


# --------------------------------------------------------------------------- #
# 图：读、翻转、改 alpha、统计
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class RgbaImage:
    """一张 8 位 RGBA 位图，**自上而下**行序（PNG 的天然行序）。"""

    width: int
    height: int
    pixels: bytes

    @property
    def pixel_count(self) -> int:
        return int(self.width) * int(self.height)


def read_png(path: str | Path) -> RgbaImage:
    """读一张 PNG（统一成 RGBA8）。"""
    try:
        from PIL import Image  # noqa: PLC0415 — 只在真读图时拉 Pillow
    except ImportError as exc:  # pragma: no cover - venv 里一定有
        raise AssetError("读 PNG 需要 Pillow", hint="产品 venv 里应该装着 pillow") from exc
    p = Path(path)
    if not p.is_file():
        raise AssetError(f"找不到 PNG：{p}", hint="--png 指一张真图片")
    with Image.open(p) as im:
        rgba = im.convert("RGBA")
        return RgbaImage(int(rgba.width), int(rgba.height), rgba.tobytes())


def replace_alpha(image: RgbaImage, value: int = 255) -> RgbaImage:
    """把 alpha 通道整条写成同一个值（RGB 一个字节不动）。"""
    if not 0 <= int(value) <= 255:
        raise ValueError(f"alpha 值要在 0..255，收到 {value!r}")
    buf = bytearray(image.pixels)
    buf[3::4] = bytes([int(value)]) * image.pixel_count
    return RgbaImage(image.width, image.height, bytes(buf))


def flip_rows(pixels: bytes, width: int, height: int) -> bytes:
    """自上而下 → 自下而上（Unity 的贴图行序；p3tex 那条链实测过方向契约）。"""
    stride = int(width) * 4
    return b"".join(pixels[y * stride: (y + 1) * stride] for y in range(int(height) - 1, -1, -1))


def alpha_stats(image: RgbaImage, cutoff: float) -> tuple[int, int, int, int]:
    """(低于阈值的像素数, 总像素, min, max) —— 阈值 = `cutoff * 255`。

    ⚠ 在**源 PNG 的 alpha** 上算（不依赖第三方解码器）。BC3 的 alpha 通道是同一张斜坡的量化版，
    实测两条口径同一个数：`_work/texd/读数.md` §10.3 —— 斜坡图都是 **50.20%**。
    """
    alpha = image.pixels[3::4]
    threshold = float(cutoff) * 255.0
    below = sum(1 for v in alpha if v < threshold - 1e-9)
    return below, len(alpha), min(alpha), max(alpha)


@dataclass(frozen=True, slots=True)
class AlphaNote:
    """alpha 门控的两个读数（对 + 不适用时也要印）。"""

    gate: float
    cutoff: float
    below: int
    total: int
    amin: int
    amax: int
    #: 门控开着、且图里确实有会被裁掉的像素 ⇒ 这条读数是"要动手"的信号。
    triggered: bool
    #: "filled"（补成全 255）/ "kept"（原样）/ "n/a"（门控没开）。
    action: str = "n/a"

    @property
    def ratio(self) -> float:
        return (self.below / self.total) if self.total else 0.0

    def summary(self) -> str:
        return (
            f"_ENABLEALPHACLIPPING={self.gate:g} · _AlphaCutoff={self.cutoff:g}"
            f"（阈值 {self.cutoff * 255:.1f}）· alpha < 阈值 = {self.below:,} / {self.total:,}"
            f" = {self.ratio:.2%} · alpha min/max = {self.amin}/{self.amax}"
        )


def alpha_note(image: RgbaImage, *, gate: float, cutoff: float, action: str = "n/a") -> AlphaNote:
    below, total, amin, amax = alpha_stats(image, cutoff)
    return AlphaNote(gate=float(gate), cutoff=float(cutoff), below=below, total=total,
                     amin=amin, amax=amax,
                     triggered=float(gate) == 1.0 and below > 0, action=action)


def encode_payload(image: RgbaImage, *, mip_count: int, format_id: int = DXT5) -> bytes:
    """PNG 像素 → BC3 ＋ 满 mip 链的字节（走产品的 `textures.encode_dxt`，⛔ 不另写一份编码器）。"""
    raw = textures.encode_dxt(
        flip_rows(image.pixels, image.width, image.height),
        int(image.width), int(image.height), int(format_id), int(mip_count),
    )
    want = textures.dxt_payload_size(image.width, image.height, format_id, mip_count)
    if len(raw) != want:
        raise AssetError(
            f"编码出来的载荷 {len(raw):,} B != 公式值 {want:,} B",
            hint="格式 / 宽高 / mip 数与字节数对不上 —— 不许硬写",
        )
    return bytes(raw)


def full_mip_count(width: int, height: int) -> int:
    """**满 mip 链**的级数 —— 一路降到 1x1 的那一级数（例：64x64 -> 7 级、128x48 -> 8 级）。

    走 `textures.mip_dims` 数出来，⛔ **不另写一遍降级规则**（降级口径只有那一份）。
    变长档拿它当默认 mip 数（PNG 尺寸与受害者不同时）。
    """
    w, h, count = int(width), int(height), 1
    while textures.mip_dims(w, h, count)[-1] != (1, 1):
        count += 1
    return count


# --------------------------------------------------------------------------- #
# 只读的源包读器（对象表 + 类型树 + 按 pathID 取字段；零 UnityPy）
# --------------------------------------------------------------------------- #
class _Reader:
    """源包的一次只读打开。全部走 `core.asset.ufs` ＋ `clone`（产品自己的读器）。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise AssetError(f"找不到件包：{self.path}", hint="--bundle 指一个造好的 UnityFS 包")
        self.reader = ufs.UfsReader(self.path)
        try:
            self.node0 = self.reader.nodes[0]
            self.info = ufs.read_serialized_file(self.reader, self.node0)
            self.meta_blob = bytes(self.reader.read_range(self.node0.offset, self.info.data_offset))
            self.meta = mw.parse_serialized_metadata(self.meta_blob)
        except Exception:
            self.reader.close()
            raise
        self.cid = {t: self.meta.types[t].class_id for t in range(len(self.meta.types))}
        self.common = clone.unitypy_common_strings()
        self.pool = clone.ref_type_pool(self.meta, common_strings=self.common)
        self.by_pid = {o[1]: o for o in self.info.objects}
        self._node_cache: dict[int, Any] = {}
        #: 流式节点（真件形状：node[1] = CAB-*.resS）。
        self.stream_node = self.reader.nodes[1] if len(self.reader.nodes) > 1 else None

    def close(self) -> None:
        self.reader.close()

    def __enter__(self) -> _Reader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- 基本读 ----
    def class_of(self, pid: int) -> int:
        return int(self.cid.get(self.by_pid[int(pid)][4], -1))

    def type_nodes(self, tid: int) -> Any:
        if tid not in self._node_cache:
            tt = self.meta.types[tid].type_tree
            self._node_cache[tid] = clone.read_type_nodes(
                struct_pack(tt.node_count, tt.string_buffer_size) + tt.nodes + tt.strings,
                common_strings=self.common,
            )
        return self._node_cache[tid]

    def raw_of(self, pid: int) -> bytes:
        o = self.by_pid[int(pid)]
        return bytes(self.reader.read_range(self.node0.offset + self.info.data_offset + o[2], o[3]))

    def fields_of(self, pid: int) -> Mapping[str, Any]:
        o = self.by_pid[int(pid)]
        return clone.read_object(self.raw_of(pid), self.type_nodes(o[4]), ref_types=self.pool)

    def encode(self, pid: int, tree: Mapping[str, Any]) -> bytes:
        o = self.by_pid[int(pid)]
        return bytes(clone.write_object(dict(tree), self.type_nodes(o[4])))

    def ids_of_class(self, class_id: int) -> list[int]:
        return [o[1] for o in self.info.objects if self.cid.get(o[4]) == class_id]

    def stream_bytes(self, offset: int, size: int) -> bytes:
        if self.stream_node is None:
            raise AssetError(
                f"{self.path.name} 只有一个节点（没有 .resS）—— 这张包的纹素不在包里",
                hint="本模块搬的是**源包 .resS 里的字节**；只有单节点的包没有可搬的东西",
            )
        return bytes(self.reader.read_range(self.stream_node.offset + int(offset), int(size)))


def struct_pack(node_count: int, string_buffer_size: int) -> bytes:
    """`clone.read_type_nodes` 要的那 8 字节头（与 p3mat/texd 同口径）。"""
    import struct  # noqa: PLC0415

    return struct.pack("<ii", int(node_count), int(string_buffer_size))


# --------------------------------------------------------------------------- #
# 找：材质 / 槽 / 容器条目（把 prefab 根认出来）
# --------------------------------------------------------------------------- #
def find_material_by_name(rd: _Reader, name: str) -> int:
    """按 `m_Name` 找材质；不唯一 / 找不到都抛（⛔ 不猜）。"""
    hits = [pid for pid in rd.ids_of_class(CLASS_MATERIAL)
            if str(rd.fields_of(pid).get("m_Name") or "") == str(name)]
    if not hits:
        names = sorted(str(rd.fields_of(p).get("m_Name") or "")
                       for p in rd.ids_of_class(CLASS_MATERIAL))
        raise AssetError(
            f"包里没有材质 {name!r}",
            hint="现有材质（前 8 个）：" + "、".join(names[:8]),
        )
    if len(hits) > 1:
        raise AssetError(
            f"材质名 {name!r} 在这个包里不唯一（{len(hits)} 个）",
            hint="⛔ 名字不唯一就不许猜；现有 pathID：" + "、".join(str(h) for h in hits[:4]),
        )
    return int(hits[0])


def color_layers(rd: _Reader, material_tree: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """这颗材质的**颜色层** `((槽名, shader 写的 desc), …)` —— 认不出就返回空元组。

    读的是材质 `m_Shader` → shader 的 `m_ParsedForm.m_PropInfo.m_Props[].m_Description`：
    实测 UnitV4_Camo 里 `Layer_A97CDC25`='Albedo'、`Layer_A97CDC25_1`='Camo'。

    ⛔ **读不到 desc ⇒ 返回空元组**（调用方据此**要求显式给 --slot** 并抛提示）。
    理由：**desc 是 shader 自己写的**（槽名里那串 hash 随 shader 变，desc 才是契约）；
    ⛔ **不许"退回第一个槽"**——读不到说明**这颗 shader 的形态我们没见过**，那时"哪个槽算颜色层"我们
    **并不知道**，随手挑一个（第一个 / 名字像的）就是**指错槽**：会把车身底图接到法线 / 遮罩那种
    数据槽上，或者只换了一半、另一半仍是别人的花纹。**认不出就说认不出。**
    """
    try:
        shp = int((material_tree.get("m_Shader") or {}).get("m_PathID", 0) or 0)
        if not shp:
            return ()
        parsed = (rd.fields_of(shp).get("m_ParsedForm") or {})
        props = ((parsed.get("m_PropInfo") or {}).get("m_Props")) or []
    except Exception:  # noqa: BLE001 — 读不到就是"认不出"，⛔ 别拿它当红判据
        return ()
    out: list[tuple[str, str]] = []
    for entry in props:
        if not isinstance(entry, Mapping):
            continue
        desc = str(entry.get("m_Description") or "")
        name = str(entry.get("m_Name") or "")
        if name and desc in COLOR_LAYER_DESCS:
            out.append((name, desc))
    return tuple(out)


def slot_pid_or_none(lists_tree: Mapping[str, Any], slot: str) -> int | None:
    """槽现在指的 pathID；**槽不在 / PPtr 是空的**都给 None（⛔ 不抛，让调用方决定）。"""
    try:
        return int(skin_install.slot_texture_pptr(lists_tree, slot)["m_PathID"])
    except Exception:  # noqa: BLE001 — 空槽是合法状态（实测 _VirtualDetailNormal 就是 0）
        return None


def material_alpha_gate(tree: Mapping[str, Any]) -> tuple[float, float]:
    """材质自己的 alpha 门控 `(_ENABLEALPHACLIPPING, _AlphaCutoff)`（认不出给 0.0）。"""
    floats = {
        str(e[0] if isinstance(e, (list, tuple)) else e.get("first")): (
            e[1] if isinstance(e, (list, tuple)) else e.get("second")
        )
        for e in ((tree.get("m_SavedProperties") or {}).get("m_Floats") or [])
    }
    gate = float(floats.get(GATE_KEY) or 0.0)
    cutoff = 0.0
    for key in CUTOFF_KEYS:
        if floats.get(key) is not None:
            cutoff = float(floats[key])
            break
    return gate, cutoff


def _pptr_pid(value: Any) -> int:
    if not isinstance(value, Mapping):
        return 0
    return int(value.get("m_PathID", 0) or 0)


def _transform_of_gameobject(rd: _Reader, go_pid: int) -> int:
    tree = rd.fields_of(go_pid)
    for entry in tree.get("m_Component") or []:
        pid = _pptr_pid((entry or {}).get("component") if isinstance(entry, Mapping) else None)
        if pid and rd.class_of(pid) == CLASS_TRANSFORM:
            return pid
    return 0


def root_gameobject_of(rd: _Reader, go_pid: int) -> int:
    """一个 GameObject 顺着 Transform.m_Father 往上走到根（容器条目的 asset 就是根）。"""
    tr = _transform_of_gameobject(rd, go_pid)
    if not tr:
        return int(go_pid)
    while True:
        tree = rd.fields_of(tr)
        father = _pptr_pid(tree.get("m_Father"))
        if not father:
            return _pptr_pid(tree.get("m_GameObject")) or int(go_pid)
        tr = father


def container_map(rd: _Reader) -> dict[int, str]:
    """`AssetBundle.m_Container` 的 `{根 pathID: 地址}` 索引。"""
    out: dict[int, str] = {}
    for obj in rd.info.objects:
        if rd.cid.get(obj[4]) != CLASS_ASSETBUNDLE:
            continue
        for entry in (rd.fields_of(obj[1]).get("m_Container") or []):
            if not isinstance(entry, Mapping):
                continue
            path = str(entry.get("first") or "")
            pid = _pptr_pid((entry.get("second") or {}).get("asset"))
            if path and pid:
                out[pid] = path
    return out


def container_for_material(
    rd: _Reader, material_pid: int, *, asset_path: str | None = None
) -> tuple[str, int]:
    """这颗材质属于**哪个 prefab**（容器地址 + 根 pathID）。

    * 给了 `asset_path` ⇒ 直接按容器地址取。**这是产品的既有约定**：
      `model_build.build_model_from_obj` 也是 caller 显式给 `asset_path`；
    * 不给 ⇒ 走「渲染器 → 根」自动发现；**发现到多个就拒绝**（⛔ 不猜）——
      真件里同一颗材质常被**机身 + 炮塔**几个 prefab 共用（实测 `DLC2_CV9035 1` 被机身、
      `…/Turrets/DLC2_CV9035/DLC2_CV9035_MK3.prefab`、`…MK4.prefab` 三个用），
      随便挑一个会产出**另一个 prefab 的件包**。

    ⚠ **本函数的第一版就是随便挑了 `hit[0]`** —— 实测直接产出了一个 **MK4 炮塔**的件包
    （125 个对象，而机身是 219 个）：装机后车是车、炮塔是炮塔，**看起来"能用"**，
    但那是**另一个 prefab**。所以这一版：唯一命中才自动用，多个命中一律 fail-closed。
    """
    entries = container_map(rd)
    if asset_path:
        resolved = resolve_container_path(entries.values(), str(asset_path))
        for root, path in entries.items():
            if resolved is not None and path == resolved:
                return path, int(root)
        raise AssetError(
            f"容器里没有地址 {asset_path!r}",
            hint=f"容器里的地址（共 {len(entries)} 条，列前 20）："
                 + "、".join(repr(x) for x in list(entries.values())[:20]),
        )
    roots: list[int] = []
    for cls in RENDERER_CLASSES:
        for rid in rd.ids_of_class(cls):
            tree = rd.fields_of(rid)
            mats = [_pptr_pid(m) for m in (tree.get("m_Materials") or [])]
            if int(material_pid) not in mats:
                continue
            go = _pptr_pid(tree.get("m_GameObject"))
            if not go:
                continue
            root = root_gameobject_of(rd, go)
            if root not in roots:
                roots.append(root)
    hit = [r for r in roots if r in entries]
    if not hit:
        raise AssetError(
            f"用这颗材质的渲染器往上走不到容器里的 prefab（候选根 {roots[:4]}）",
            hint="这个包可能不是一个「单位模型包」；⛔ 别拿一个非模型包来跑 texture apply",
        )
    if len(hit) > 1:
        raise AssetError(
            f"这颗材质被 {len(hit)} 个 prefab 用（容器条目）："
            + "、".join(repr(entries[r]) for r in hit),
            hint="⛔ 不许猜：请显式给 asset_path（与 newasset add-model / build-model 同名同义），"
                 "或者先用 build-model 把源缩成一个 prefab 的件包",
        )
    root = hit[0]
    return entries[root], int(root)


# --------------------------------------------------------------------------- #
# pair 形状过桥（clone 的读器给 {"first","second"}；产品那几个槽入口只认 [k, v]）
# --------------------------------------------------------------------------- #
def _pair_key(entry: Any) -> str:
    return str(entry.get("first") if isinstance(entry, Mapping) else entry[0])


def _pair_value(entry: Any) -> Any:
    return entry.get("second") if isinstance(entry, Mapping) else entry[1]


def _saved(tree: Mapping[str, Any]) -> Mapping[str, Any]:
    props = tree.get("m_SavedProperties")
    if not isinstance(props, Mapping):
        raise AssetError("这个材质没有 m_SavedProperties", hint="传进来的不是材质 typetree")
    return props


def as_pair_lists(tree: Mapping[str, Any]) -> dict[str, Any]:
    """`clone` 的形状 → 产品槽入口认的形状（只动 m_TexEnvs 那张表）。"""
    props = dict(_saved(tree))
    envs = props.get("m_TexEnvs")
    if not isinstance(envs, Sequence) or isinstance(envs, (str, bytes)):
        raise AssetError("这个材质的 m_TexEnvs 不是序列", hint="先看清形状再改，⛔ 不许盲改")
    props["m_TexEnvs"] = [
        [ _pair_key(e), _pair_value(e) ] if isinstance(e, Mapping) else e for e in envs
    ]
    out = dict(tree)
    out["m_SavedProperties"] = props
    return out


def as_pair_dicts(tree: Mapping[str, Any]) -> dict[str, Any]:
    """产品槽入口的形状 → `clone` 的形状（同上，只动 m_TexEnvs）。"""
    props = dict(_saved(tree))
    envs = props.get("m_TexEnvs")
    if not isinstance(envs, Sequence) or isinstance(envs, (str, bytes)):
        raise AssetError("这个材质的 m_TexEnvs 不是序列", hint="先看清形状再改，⛔ 不许盲改")
    props["m_TexEnvs"] = [{"first": _pair_key(e), "second": _pair_value(e)} for e in envs]
    out = dict(tree)
    out["m_SavedProperties"] = props
    return out


class _TypeTreeSource:
    """给 `skin_install.guard_slot_target` 用的薄适配器（它只要 `read_typetree`）。"""

    def __init__(self, rd: _Reader) -> None:
        self._rd = rd

    def read_typetree(self, path_id: int) -> Mapping[str, Any]:
        return self._rd.fields_of(int(path_id))


# --------------------------------------------------------------------------- #
# ★ 出包读回的尺子（工单 §7 坑 2 的另一半：写完之后**必须**量一次）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class TextureReading:
    """从**一个包**里读回的一颗 Texture2D 的读数（判据全在 `texture_readback_problems`）。"""

    path_id: int
    class_id: int
    name: str
    width: int
    height: int
    texture_format: int
    mip_count: int
    complete_image_size: int
    stream_path: str
    offset: int
    size: int
    #: 本包里 `.resS` 那个节点的名字 / 长度（`m_StreamData.size` 必须等于后者）。
    stream_node_path: str
    stream_node_size: int
    #: `.resS` 里 `[ offset, offset + size )` 那一段字节（读不出就是 b""）。
    pixels: bytes = field(repr=False, default=b"")

    def as_dict(self) -> dict[str, Any]:
        return {
            "path_id": self.path_id,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "format": self.texture_format,
            "mip_count": self.mip_count,
            "complete_image_size": self.complete_image_size,
            "stream": {"path": self.stream_path, "offset": self.offset, "size": self.size},
            "res_s": {"path": self.stream_node_path, "size": self.stream_node_size},
        }

    def pixels_sha256(self) -> str:
        return hashlib.sha256(self.pixels).hexdigest()


def _reading_of(rd: _Reader, path_id: int, *, with_pixels: bool) -> TextureReading:
    """**已经开着的** `_Reader` 上读一颗 Texture2D —— 所有读数入口共用的那一份。"""
    if int(path_id) not in rd.by_pid:
        raise AssetError(
            f"这个包里没有 pathID={int(path_id)}",
            hint="尺子量的是**出包里**那颗贴图 —— pathID 别抄源包的",
        )
    cls = rd.class_of(int(path_id))
    if cls != CLASS_TEXTURE2D:
        raise AssetError(f"pathID={int(path_id)} 不是 Texture2D（classID {cls}）")
    tree = rd.fields_of(int(path_id))
    sd = dict(tree.get("m_StreamData") or {})
    node = rd.stream_node
    offset, size = int(sd.get("offset", 0) or 0), int(sd.get("size", 0) or 0)
    pixels = b""
    if with_pixels and size > 0 and node is not None and offset + size <= node.size:
        pixels = rd.stream_bytes(offset, size)
    return TextureReading(
        path_id=int(path_id),
        class_id=cls,
        name=str(tree.get("m_Name") or ""),
        width=int(tree.get("m_Width") or 0),
        height=int(tree.get("m_Height") or 0),
        texture_format=int(tree.get("m_TextureFormat") or 0),
        mip_count=int(tree.get("m_MipCount") or 0),
        complete_image_size=int(tree.get("m_CompleteImageSize") or 0),
        stream_path=str(sd.get("path") or ""),
        offset=offset,
        size=size,
        stream_node_path="" if node is None else str(node.path),
        stream_node_size=0 if node is None else int(node.size),
        pixels=bytes(pixels),
    )


def read_texture_back(
    bundle: str | Path, path_id: int, *, with_pixels: bool = True
) -> TextureReading:
    """**只读**：从一个（多半是刚出的）包里读回一颗 Texture2D 的六个读数。

    ⛔ 走产品自己的读器（`_Reader`）—— 这把尺子要能**独立于写那一侧**的实现；
    第三方那条独立读路（UnityPy）在验收脚本里另跑一遍，两条对上才算数。
    """
    with _Reader(bundle) as rd:
        return _reading_of(rd, int(path_id), with_pixels=with_pixels)


def read_stream_textures(bundle: str | Path) -> tuple[TextureReading, ...]:
    """**一次开包**读回包里**所有流式**贴图（`m_StreamData.size > 0`）的读数。

    逐张开包那个写法在真件上要开十几次（每次都要解析整张元数据区）—— A5 自证要一次拿全。
    """
    with _Reader(bundle) as rd:
        out: list[TextureReading] = []
        for pid in rd.ids_of_class(CLASS_TEXTURE2D):
            tree = rd.fields_of(int(pid))
            if int((tree.get("m_StreamData") or {}).get("size", 0) or 0) <= 0:
                continue
            out.append(_reading_of(rd, int(pid), with_pixels=True))
        return tuple(out)


def texture_readback_problems(
    reading: TextureReading,
    *,
    expect_width: int | None = None,
    expect_height: int | None = None,
    expect_format: int = DXT5,
    expect_mip_count: int | None = None,
    expect_payload: bytes | None = None,
    expect_stream_node_size: int | None = None,
) -> tuple[str, ...]:
    """**纯函数尺子**：空元组 = 绿；非空 = 一行行问题（每一条都能单独红）。

    七条判据（AGENTS §0.9 动作 2：喂一个已知违规的点进去**必须报红**）：

    1. 类对不对（必须是 Texture2D）；
    2. `m_Width` / `m_Height` == 我们要的那张图（给了期望才比）；
    3. `m_TextureFormat` == 期望格式；
    4. `m_MipCount` == 期望级数（给了才比）；
    5. ★ **自洽**：`m_CompleteImageSize` == `dxt_payload_size(w, h, 格式, mip 数)`
       —— **「只改 m_TextureFormat、其余留旧值」就是死在这一条上**；
    6. `m_StreamData.size` == 未压缩总量 == 我们那份载荷的长度、`offset == 0`，且区间
       `[offset, offset+size)` **装得进本包 `.resS` 节点**（给了 `expect_stream_node_size` 就再比
       一次节点总长 —— A5 自足化之后节点里还装着别张图的纹素，⛔ 别再拿"节点 == 这一段"当判据）；
    7. `.resS` 里那一段字节与我们那份载荷**逐字节相同**（给了载荷才比）。
    """
    problems: list[str] = []
    if reading.class_id != CLASS_TEXTURE2D:
        problems.append(f"对象不是 Texture2D（classID {reading.class_id}）")
    if expect_width is not None and reading.width != int(expect_width):
        problems.append(f"m_Width {reading.width} != 我们要的 {int(expect_width)}")
    if expect_height is not None and reading.height != int(expect_height):
        problems.append(f"m_Height {reading.height} != 我们要的 {int(expect_height)}")
    if reading.texture_format != int(expect_format):
        problems.append(
            f"m_TextureFormat {reading.texture_format} != {int(expect_format)}"
            f"（{textures.format_name(expect_format)}）"
        )
    if expect_mip_count is not None and reading.mip_count != int(expect_mip_count):
        problems.append(f"m_MipCount {reading.mip_count} != 我们要的 {int(expect_mip_count)}")
    block = textures.BLOCK_BYTES.get(int(reading.texture_format))
    if block is None:
        problems.append(
            f"格式 {reading.texture_format}"
            f"（{textures.format_name(reading.texture_format)}）不是能算未压缩总量的非 Crunched"
            "格式 —— 本模块只出 DXT1/DXT5"
        )
    elif reading.width > 0 and reading.height > 0 and reading.mip_count > 0:
        want = textures.dxt_payload_size(
            reading.width, reading.height, reading.texture_format, reading.mip_count
        )
        if reading.complete_image_size != want:
            problems.append(
                f"m_CompleteImageSize {reading.complete_image_size:,} != 未压缩总量 {want:,}"
                f"（{reading.width}x{reading.height} 格式 {reading.texture_format}"
                f" mip {reading.mip_count}）—— ★ 这一条就是「只改格式、长度留旧值」的死因"
            )
        if reading.size != want:
            problems.append(f"m_StreamData.size {reading.size:,} != 未压缩总量 {want:,}")
    if reading.offset != 0:
        problems.append(
            f"m_StreamData.offset {reading.offset} != 0（新像素只进本包 .resS，没有别的落点）"
        )
    if reading.offset + reading.size > reading.stream_node_size:
        problems.append(
            f"m_StreamData 区间 [{reading.offset:,}, {reading.offset + reading.size:,})"
            f" 超出本包 .resS 节点（{reading.stream_node_size:,} B）"
        )
    if (expect_stream_node_size is not None
            and reading.stream_node_size != int(expect_stream_node_size)):
        problems.append(
            f"本包 .resS 节点 size {reading.stream_node_size:,} != 我们要的"
            f" {int(expect_stream_node_size):,}"
        )
    if expect_payload is not None:
        payload = bytes(expect_payload)
        if reading.size != len(payload):
            problems.append(f"m_StreamData.size {reading.size:,} != 载荷 {len(payload):,} B")
        if reading.pixels and reading.pixels != payload:
            problems.append(
                f".resS 里那一段像素与载荷不同（读回 {len(reading.pixels):,} B"
                f" sha256 {reading.pixels_sha256()[:16]} vs 载荷 {len(payload):,} B"
                f" sha256 {hashlib.sha256(payload).hexdigest()[:16]}）"
            )
    return tuple(problems)


def verify_texture_out(
    bundle: str | Path,
    path_id: int,
    *,
    expect_width: int | None = None,
    expect_height: int | None = None,
    expect_format: int = DXT5,
    expect_mip_count: int | None = None,
    expect_payload: bytes | None = None,
) -> tuple[str, ...]:
    """读 + 判一步到位：`read_texture_back` ＋ `texture_readback_problems`（空 = 绿）。"""
    return texture_readback_problems(
        read_texture_back(bundle, path_id),
        expect_width=expect_width,
        expect_height=expect_height,
        expect_format=expect_format,
        expect_mip_count=expect_mip_count,
        expect_payload=expect_payload,
    )


# --------------------------------------------------------------------------- #
# ★ A5 自足化：闭包里**其它**流式贴图的纹素怎么搬、搬不动怎么办
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class StreamMove:
    """一次**原样搬迁**：某张流式贴图的纹素从源包 .resS 搬进了本包 .resS。

    `sha256` 是**搬的那一段字节**的指纹 —— 出包读回时拿它比，比的是"搬对了没有"，
    而不是"我以为我搬对了"。
    """

    path_id: int
    name: str
    source_offset: int
    offset: int
    size: int
    sha256: str = field(repr=False, default="")

    def line(self) -> str:
        return (f"· 搬 {self.name!r}（{self.path_id}）源 [{self.source_offset:,},"
                f" {self.source_offset + self.size:,}) -> 本包 @{self.offset:,}"
                f"  {self.size:,} B  sha256 {self.sha256[:16]}")


@dataclass(frozen=True, slots=True)
class StreamStuck:
    """**搬不动**的那一张 —— ⛔ 如实报出来，一条都不许静默算绿。"""

    path_id: int
    name: str
    path: str
    source_offset: int
    size: int
    reason: str

    def line(self) -> str:
        return (f"✗ 搬不动 {self.name!r}（{self.path_id}）{self.size:,} B"
                f" @{self.source_offset:,} —— {self.reason}")


@dataclass(frozen=True, slots=True)
class ForeignStream:
    """**出包读回**发现的"纹素不在本包"的流式贴图（A5 那把尺子的一行）。"""

    path_id: int
    name: str
    path: str
    offset: int
    size: int
    reason: str


def foreign_streams(bundle: str | Path) -> tuple[ForeignStream, ...]:
    """**A5 的尺子（读一个包）**：空元组 = 包里每张流式贴图的纹素都落在本包 .resS 里。

    判据与 `unit_bundle.verify_unit_bundle` 的 A5 同一口径（path 的节点名要落在本包的节点里），
    **多一层区间检查**：节点名对上了、但 `offset + size` 超出那个节点长度的，一样取不到像素
    （⛔ 只认名字那一半会漏掉这一档，而引擎取不到纹素是**静默**的）。
    """
    with _Reader(bundle) as rd:
        nodes = {str(getattr(n, "path", "") or ""): int(n.size) for n in rd.reader.nodes}
        node = rd.stream_node
        res_s_name = "" if node is None else str(node.path)
        out: list[ForeignStream] = []
        for pid in rd.ids_of_class(CLASS_TEXTURE2D):
            tree = rd.fields_of(int(pid))
            sd = dict(tree.get("m_StreamData") or {})
            size = int(sd.get("size", 0) or 0)
            if size <= 0:
                continue                      # 不是流式贴图（内联 / 空）⇒ 不归 A5 这条管
            path = str(sd.get("path") or "")
            offset = int(sd.get("offset", 0) or 0)
            cab = path.rsplit("/", 1)[-1] if path else ""
            name = str(tree.get("m_Name") or "?")
            if not cab or cab not in nodes:
                reason = (f"m_StreamData.path={path!r} 不在本包的节点里" if path
                          else "m_StreamData.path 是空的")
            elif cab != res_s_name:
                reason = f"m_StreamData.path 指的是本包的**非流式**节点 {cab!r}"
            elif offset < 0 or offset + size > nodes[cab]:
                reason = (f"区间 [{offset:,}, {offset + size:,}) 超出本包 .resS 节点"
                          f"（{nodes[cab]:,} B）")
            else:
                continue                      # 绿：落点与区间都对
            out.append(ForeignStream(path_id=int(pid), name=name, path=path, offset=offset,
                                     size=size, reason=reason))
        return tuple(out)


def verify_streams_out(
    bundle: str | Path,
    *,
    moved: Sequence[StreamMove] = (),
    expect_foreign: Collection[int] = (),
) -> tuple[str, ...]:
    """出包读回的**自足性复核**（空 = 绿）—— `apply_texture` 落盘后自己跑的那一道。

    * 每张**被搬**的贴图：还在、`offset`/`size` 与记录一致、那段字节的 sha256 与搬迁时相同；
    * 除 `expect_foreign` 里那些（**已如实报过**搬不动的）之外，⛔ 不许有别的流式贴图落在包外。
    """
    problems: list[str] = []
    readings = {r.path_id: r for r in read_stream_textures(bundle)}
    for mv in moved:
        rb = readings.get(int(mv.path_id))
        if rb is None:
            problems.append(
                f"pathID={mv.path_id}（{mv.name!r}）搬完之后在出包里找不到 / 不再是流式贴图"
            )
            continue
        if (rb.offset, rb.size) != (int(mv.offset), int(mv.size)):
            problems.append(
                f"{mv.name!r} 搬完 offset/size = {rb.offset}/{rb.size}"
                f" != 记录的 {mv.offset}/{mv.size}"
            )
        if rb.pixels_sha256() != mv.sha256:
            problems.append(
                f"{mv.name!r} 搬完那段字节 sha256 {rb.pixels_sha256()[:16]}"
                f" != 搬运时的 {mv.sha256[:16]}"
            )
    allowed = {int(p) for p in expect_foreign}
    for f in foreign_streams(bundle):
        if f.path_id in allowed:
            continue
        problems.append(f"{f.name!r}（{f.path_id}）的纹素不在本包：{f.reason}")
    return tuple(problems)


# --------------------------------------------------------------------------- #
# 结果对象
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class AppliedEdit:
    """一处被改的对象（等长 / 变长都收，长度变化记在 `bytes_before` / `bytes_after`）。"""

    path_id: int
    name: str
    type_name: str
    what: str
    changed_bytes: int
    #: 改前 / 改后这个对象的字节数（不相等 = 这个对象**变长了 / 变短了**）。
    bytes_before: int = 0
    bytes_after: int = 0


@dataclass(frozen=True, slots=True)
class TextureApplyPlan:
    """一次 `texture apply` 的全部读数（计划与真写读的是同一份）。"""

    bundle: Path
    out: Path
    bundle_name: str
    node_path: str
    stream_path: str
    container: tuple[tuple[str, int], ...]
    root: int
    material_name: str
    material_path_id: int
    slot: str
    slot_before: int
    slot_after: int
    victim_path_id: int
    victim_name: str
    victim_format_before: int
    victim_stream_before: int
    image_in: RgbaImage
    image_used: RgbaImage
    note: AlphaNote
    alpha_mode: str
    mip_count: int
    payload: bytes = field(repr=False, default=b"")
    objects: int = 0
    edits: tuple[AppliedEdit, ...] = ()
    dangling: tuple[int, ...] = ()
    data: bytes = field(repr=False, default=b"")
    seconds: float = 0.0
    #: 这颗材质的**颜色层**（槽名, shader 自写 desc）—— 不给 --slot 时全指的就是它们
    color_layers: tuple[tuple[str, str], ...] = ()
    #: 这次真的指过去的槽（⛔ 只列变了/确认指着的那些）
    slots_pointed: tuple[str, ...] = ()
    #: 要印给人看的警告（例：只指了一个槽，另一个颜色层没换 ⇒ 会被原涂装调制）
    warnings: tuple[str, ...] = ()
    #: 受害者改前的形状（变长档要拿它和"我们要的"对比印出来）
    victim_width_before: int = 0
    victim_height_before: int = 0
    victim_mips_before: int = 0
    #: 落盘之后从**出包**里读回来的那颗贴图（`apply_texture` 才有；纯干跑是 None）
    readback: TextureReading | None = None
    #: 出包读回复核的问题（空 = 六个读数逐条对上）
    problems: tuple[str, ...] = ()
    #: ★ A5 自足化：**原样搬**进本包 .resS 的那些流式贴图（逐段追加，16 字节对齐）
    stream_moved: tuple[StreamMove, ...] = ()
    #: ★ 搬不动的那几张 —— ⛔ 如实报出来，一条都不许静默算绿
    stream_stuck: tuple[StreamStuck, ...] = ()
    #: 本包 .resS 节点的总长度（= 我们的载荷 ＋ 所有搬进来的段 ＋ 对齐补位）
    stream_bytes: int = 0

    @property
    def payload_bytes(self) -> int:
        return len(self.payload)

    @property
    def shape_unchanged(self) -> bool:
        """尺寸与 mip 数是不是与受害者**完全一样**（一样 = 走的是等长档那条路）。"""
        return (
            self.image_used.width == self.victim_width_before
            and self.image_used.height == self.victim_height_before
            and self.mip_count == self.victim_mips_before
        )

    @property
    def self_sufficient(self) -> bool:
        """出包里**没有**纹素落在包外的流式贴图（A5 绿）—— 搬不动的有报出来就不是它了。"""
        return not self.stream_stuck

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def alpha_filled(self) -> bool:
        return self.alpha_mode == "opaque" and self.note.action == "filled"

    def lines(self) -> tuple[str, ...]:
        """给人读的读数（CLI 与用例共用一份话术）。"""
        layers = "、".join(f"{n}({d})" for n, d in self.color_layers) or "（读不到 desc）"
        rows = [
            f"[贴图进包] {self.bundle.name}  ->  {self.out.name}",
            f"  颜色层    : {layers}",
            f"  这次指的槽: {'、'.join(self.slots_pointed) or '（一个都没指）'}",
            f"  材质      : {self.material_name!r}（pathID {self.material_path_id}）",
            f"  槽        : {self.slot}  {self.slot_before} -> {self.slot_after}",
            f"  受害者    : {self.victim_name!r}（pathID {self.victim_path_id}）"
            f"  fmt {self.victim_format_before} -> {DXT5}"
            f"  流式 {self.victim_stream_before:,} -> {self.payload_bytes:,} B",
            f"  图        : {self.image_in.width}x{self.image_in.height}"
            f"  mip={self.mip_count}  载荷 {self.payload_bytes:,} B"
            f"  sha256 {self.payload_sha256[:16]}",
            f"  受害者形状: {self.victim_width_before}x{self.victim_height_before}"
            f" mip {self.victim_mips_before}  ->  "
            f"{self.image_used.width}x{self.image_used.height} mip {self.mip_count}"
            + ("（等长）" if self.shape_unchanged else "（★ 变长：六个数一起落位）"),
            f"  alpha     : {self.note.summary()}",
        ]
        if self.alpha_filled:
            rows.append(
                "  ★★ 目标材质的 alpha 裁剪是**开着**的（_ENABLEALPHACLIPPING=1）——"
                f"你这张图有 {self.note.ratio:.2%} 的像素 alpha 低于阈值，"
                "那些面会被裁成**洞**；本命令**已自动把 alpha 补成全 255**（RGB 一个字节没动）。"
            )
        elif self.note.gate == 1.0:
            rows.append("  alpha     : 门控开着，但这张图的 alpha 全部达标 ⇒ 原样编码")
        else:
            rows.append("  alpha     : 目标材质不裁（_ENABLEALPHACLIPPING != 1）⇒ 不动 alpha")
        rows.append(f"  容器      : {self.container[0][0]!r}（根 {self.root}）")
        rows.append(f"  改了几处  : {len(self.edits)} 个对象")
        for e in self.edits:
            grew = ("" if e.bytes_after == e.bytes_before
                    else f"，对象 {e.bytes_before:,} -> {e.bytes_after:,} B")
            rows.append(f"     · {e.type_name} {e.name!r}（{e.path_id}）"
                        f"{e.what}，动 {e.changed_bytes} 字节{grew}")
        rows.extend(self.warnings)
        rows.append(f"  A5 自足化 : 搬了 {len(self.stream_moved)} 张流式贴图的纹素进本包 .resS"
                    f"（.resS 共 {self.stream_bytes:,} B / 每段按 {STREAM_APPEND_ALIGN} 字节对齐）"
                    + (f"；★ 还有 {len(self.stream_stuck)} 张**搬不动**" if self.stream_stuck
                       else "；包外流式贴图 0 张"))
        for mv in self.stream_moved:
            rows.append(f"     {mv.line()}")
        for st in self.stream_stuck:
            rows.append(f"     {st.line()}")
        rows.append(f"  悬空 PPtr : {len(self.dangling)}（必须 0）")
        if self.readback is not None:
            if self.problems:
                rows.append(f"  ★ 出包复核: {len(self.problems)} 条对不上")
            else:
                rows.append("  ★ 出包复核: 出包读回，六读数逐条对上（尺寸 / 格式 / mip /"
                            " 未压缩总量 / offset / .resS size ＋ 逐字节像素）")
            for problem in self.problems:
                rows.append(f"     ✗ {problem}")
        else:
            rows.append("  出包复核  : 干跑不落盘 ⇒ 不读回（真写那条路会自己量一遍）")
        rows.append(f"  出包      : {len(self.data):,} B  sha256 {self.sha256[:16]}…")
        return tuple(rows)

    def document(self) -> dict[str, Any]:
        """给 `--json` 的一份（键名与 lines 一一对应）。"""
        return {
            "bundle": str(self.bundle),
            "out": str(self.out),
            "bundle_name": self.bundle_name,
            "node_path": self.node_path,
            "stream_path": self.stream_path,
            "container": [list(x) for x in self.container],
            "root": self.root,
            "material": {"name": self.material_name, "path_id": self.material_path_id},
            "slot": {"name": self.slot, "before": self.slot_before, "after": self.slot_after},
            "color_layers": [{"name": n, "desc": d} for n, d in self.color_layers],
            "slots_pointed": list(self.slots_pointed),
            "warnings": list(self.warnings),
            "victim": {"name": self.victim_name, "path_id": self.victim_path_id,
                       "format_before": self.victim_format_before,
                       "stream_before": self.victim_stream_before,
                       "width_before": self.victim_width_before,
                       "height_before": self.victim_height_before,
                       "mip_before": self.victim_mips_before,
                       "shape_changed": not self.shape_unchanged},
            "readback": None if self.readback is None else self.readback.as_dict(),
            "problems": list(self.problems),
            "streams": {
                "bytes": self.stream_bytes,
                "self_sufficient": self.self_sufficient,
                "moved": [{"path_id": m.path_id, "name": m.name,
                           "source_offset": m.source_offset, "offset": m.offset,
                           "size": m.size, "sha256": m.sha256} for m in self.stream_moved],
                "stuck": [{"path_id": s.path_id, "name": s.name, "path": s.path,
                           "source_offset": s.source_offset, "size": s.size,
                           "reason": s.reason} for s in self.stream_stuck],
            },
            "image": {"width": self.image_in.width, "height": self.image_in.height,
                      "mip_count": self.mip_count},
            "alpha": {"mode": self.alpha_mode, "gate": self.note.gate, "cutoff": self.note.cutoff,
                      "below": self.note.below, "total": self.note.total,
                      "ratio": round(self.note.ratio, 6), "action": self.note.action,
                      "filled": self.alpha_filled},
            "payload": {"bytes": self.payload_bytes, "sha256": self.payload_sha256},
            "edits": [{"path_id": e.path_id, "name": e.name, "type": e.type_name,
                       "what": e.what, "changed_bytes": e.changed_bytes,
                       "bytes_before": e.bytes_before, "bytes_after": e.bytes_after}
                      for e in self.edits],
            "objects": self.objects,
            "dangling": list(self.dangling),
            "out_bytes": len(self.data),
            "out_sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class EncodeReport:
    """`texture encode` 的读数（门控那两行不适用时印 n/a）。"""

    png: Path
    image: RgbaImage
    mip_count: int
    payload: bytes = field(repr=False, default=b"")
    out: Path | None = None
    note: AlphaNote = field(default_factory=lambda: AlphaNote(  # type: ignore[arg-type]
        gate=0.0, cutoff=0.0, below=0, total=0, amin=0, amax=0, triggered=False, action="n/a"))

    @property
    def payload_bytes(self) -> int:
        return len(self.payload)

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()

    def lines(self) -> tuple[str, ...]:
        rows = [
            f"[贴图编码] {self.png.name}  {self.image.width}x{self.image.height}",
            f"  格式      : DXT5 / BC3（fmt {DXT5}）· mip {self.mip_count} 级",
            f"  载荷      : {self.payload_bytes:,} B  sha256 {self.payload_sha256[:16]}…",
            f"  alpha 门控: n/a（encode 不看材质）· 图自己的 alpha < 127.5 = "
            f"{self.note.below:,} / {self.note.total:,} = {self.note.ratio:.2%}"
            f"（alpha min/max = {self.note.amin}/{self.note.amax}）",
        ]
        if self.out is not None:
            rows.append(f"  落点      : {self.out}")
        return tuple(rows)


# --------------------------------------------------------------------------- #
# 两条主链
# --------------------------------------------------------------------------- #
def plan_texture_apply(
    *,
    bundle: str | Path,
    material: str,
    slot: str | None = None,
    png: str | Path = "",
    out: str | Path,
    victim: int | None = None,
    alpha: str = "opaque",
    asset_path: str | None = None,
    mips: int | None = None,
) -> TextureApplyPlan:
    """把「一张 PNG 的像素」算成「一个新的件包」——**只读源包，一个字节都不写**。

    `mips` 不给（None）时：PNG 尺寸与受害者**相同** ⇒ 照抄受害者的 mip 数（等长档那条路的
    字节因此逐字节不变）；**不同** ⇒ 用**满链**（`full_mip_count`，逐级编到 1x1）。
    给了就按给的，但必须 `<= 满链`（⛔ Unity 没有比 1x1 更小的级）。
    """
    if alpha not in ALPHA_MODES:
        raise AssetError(f"alpha 口径只有 {ALPHA_MODES}，收到 {alpha!r}")
    out_path = Path(out)
    image_in = read_png(png)
    with _Reader(bundle) as rd:
        mat_pid = find_material_by_name(rd, material)
        mat_tree = rd.fields_of(mat_pid)
        mat_lists = as_pair_lists(mat_tree)
        layers = color_layers(rd, mat_tree)
        warnings: list[str] = []
        # ---- 要指哪几个槽 ----
        if slot:
            slots = [str(slot)]
            others = [(n, d) for n, d in layers if n != str(slot)]
            if others:
                warnings.append(WARN_OTHER_COLOR_LAYER.format(slot=str(slot), other=others[0][0],
                                                               desc=others[0][1]))
        elif layers:
            slots = [n for n, _d in layers]
        else:
            raise AssetError(
                f"材质 {material!r} 的 shader 读不到颜色层描述（{'/'.join(COLOR_LAYER_DESCS)}）",
                hint="这种材质**必须显式给 --slot**（⛔ 不猜槽名：槽名里那串 hash 会随 shader 变）",
            )
        # ---- 受害者：给了就用；没给 = 主颜色层（Albedo）现在指的那张 ----
        if victim is None:
            primary = next((n for n, d in layers if d == PRIMARY_DESC), slots[0] if slots else "")
            if primary:
                cur = slot_pid_or_none(mat_lists, primary)
                if cur is None:
                    raise AssetError(
                        f"颜色层 {primary!r} 现在没有指任何贴图（空槽）⇒ 认不出该把字节放进哪张",
                        hint="显式给 --victim <pathID>（包里既有的那张贴图）",
                    )
                victim = cur
        slot_before = slot_pid_or_none(mat_lists, slots[0]) or 0
        victim_pid = int(victim)
        if victim_pid not in rd.by_pid:
            raise AssetError(f"这个包里没有 pathID={victim_pid}",
                             hint="--victim 要指包里既有的那张贴图")
        if rd.class_of(victim_pid) != CLASS_TEXTURE2D:
            raise AssetError(
                f"pathID={victim_pid} 不是 Texture2D（classID {rd.class_of(victim_pid)}）",
                hint="--victim 只能指一张贴图",
            )
        vt = rd.fields_of(victim_pid)
        v_name = str(vt.get("m_Name") or "")
        v_w, v_h = int(vt.get("m_Width") or 0), int(vt.get("m_Height") or 0)
        v_mips = int(vt.get("m_MipCount") or 0)
        sd = dict(vt.get("m_StreamData") or {})
        v_size = int(sd.get("size", 0) or 0)
        if v_size <= 0:
            raise AssetError(
                f"受害者 {v_name!r} 不是流式贴图（m_StreamData.size = 0）",
                hint="本模块带进去的是流式字节；要目标贴图本来就是流式的（真件 710/710 都是）",
            )
        # ---- 尺寸 / mip：本版**允许与受害者不同**（变长档：六个数一起落位）----
        same_size = (image_in.width, image_in.height) == (v_w, v_h)
        chain = full_mip_count(image_in.width, image_in.height)
        if mips is not None:
            want_mips = int(mips)
            if want_mips < 1:
                raise AssetError(f"mip 数要 >= 1，收到 {want_mips}", hint="--mips 至少给 1")
            if want_mips > chain:
                raise AssetError(
                    f"mip 数 {want_mips} 超过满链 {chain}"
                    f"（{image_in.width}x{image_in.height} 一路降到 1x1 只有 {chain} 级）",
                    hint="⛔ Unity 没有比 1x1 更小的级；给一个 <= 满链的数，或者去掉 --mips",
                )
        elif same_size:
            if v_mips <= 0:
                raise AssetError(f"受害者 {v_name!r} 的 m_MipCount={v_mips}，认不出来",
                                 hint="⛔ 不猜 mip 数：显式给 --mips")
            want_mips = v_mips
        else:
            want_mips = chain
        gate, cutoff = material_alpha_gate(mat_tree)
        pre = alpha_note(image_in, gate=gate, cutoff=cutoff)
        if pre.triggered and alpha == "keep":
            raise AssetError(
                f"目标材质 {material!r} 的 _ENABLEALPHACLIPPING=1（阈值 {cutoff * 255:.1f}），"
                f"而这张图有 **{pre.ratio:.2%}** 的像素 alpha 低于阈值 —— 那些面会被裁成洞",
                hint="要么去掉 --alpha keep（默认会把 alpha 补成全 255，RGB 不动），"
                     "要么换一张 alpha 达标的图",
            )
        filled = bool(pre.triggered and alpha == "opaque")
        image_used = replace_alpha(image_in) if filled else image_in
        action = "filled" if filled else ("kept" if float(gate) == 1.0 else "n/a")
        note = alpha_note(image_in, gate=gate, cutoff=cutoff, action=action)
        payload = encode_payload(image_used, mip_count=want_mips)

        bundle_name = out_path.name
        node_path = builder.node_path_for(bundle_name)
        stream_path = f"{ARCHIVE_PREFIX}{node_path}/{node_path}.resS"
        # ⚠ 以前这里有一条「新旧路径必须等长」的守卫 —— 变长档**删掉**：对象长度变了由
        #   builder 自己重排对象表（`AppliedEdit.bytes_before/after` 会把长度变化记下来）。
        old_path = str(sd.get("path") or "")
        asset_path, root = container_for_material(rd, mat_pid, asset_path=asset_path)
        # ---- 闭包：除被改的那几个，其余逐字节原样搬 ----
        seen, queue = [], [int(root)]
        while queue:
            pid = queue.pop()
            if pid in seen or pid not in rd.by_pid:
                continue
            seen.append(pid)
            tree = rd.fields_of(pid)

            def visit(value: Any) -> None:
                if isinstance(value, Mapping):
                    if "m_PathID" in value and "m_FileID" in value:
                        fid, pid2 = int(value.get("m_FileID", 0)), int(value.get("m_PathID", 0))
                        if fid == 0 and pid2 in rd.by_pid:
                            queue.append(int(value["m_PathID"]))
                    else:
                        for sub in value.values():
                            visit(sub)
                elif isinstance(value, list):
                    for sub in value:
                        visit(sub)

            visit(tree)

        # ---- 等长编辑：受害者 +（必要时）材质槽 ----
        edits: dict[int, Mapping[str, Any]] = {}
        new_victim = dict(vt)
        new_sd = dict(sd)
        new_sd.update(path=stream_path, offset=0, size=len(payload))
        new_victim["m_StreamData"] = new_sd
        # ★ 工单 §7 坑 2：下面这**六个数必须落在同一次写回里**（只改 m_TextureFormat = 必坏）。
        #   m_Width / m_Height / m_MipCount 也在这里落位 —— 尺寸与受害者相同也照写一次
        #   （写同样的值 ⇒ 字节逐字节不变，等长档那条路因此不受影响）。
        new_victim["m_Width"] = int(image_used.width)
        new_victim["m_Height"] = int(image_used.height)
        new_victim["m_MipCount"] = int(want_mips)
        new_victim["m_TextureFormat"] = DXT5
        new_victim["m_CompleteImageSize"] = len(payload)
        if int(new_victim.get("m_ColorSpace", 0) or 0) != 1:
            new_victim["m_ColorSpace"] = 1
        # 幂等：victim 已经指着**本包 .resS 的同一段**（再跑一次 / 多槽链跑第二次）⇒ 这一处不写
        already = (
            old_path == stream_path
            and int(sd.get("offset", 0) or 0) == 0
            and int(v_size) == len(payload)
            and int(vt.get("m_TextureFormat") or 0) == DXT5
            and int(vt.get("m_CompleteImageSize") or 0) == len(payload)
            and int(vt.get("m_ColorSpace", 0) or 0) == 1
            and (v_w, v_h) == (int(image_used.width), int(image_used.height))
            and int(v_mips) == int(want_mips)
            and rd.encode(int(victim_pid), new_victim) == rd.raw_of(int(victim_pid))
        )
        if not already:
            edits[int(victim_pid)] = new_victim

        # ---- ★ A5 自足化：闭包里**其它**流式贴图的纹素也搬进本包 .resS ----
        # 为什么必须搬：新包的节点名由包名决定（builder.node_path_for），源包里指着**它自己**
        # .resS 的那些贴图，在新包里指的是一个**不存在的节点** —— 引擎对丢纹素是**静默**的，
        # 装机后就是白图（A5 那条红；判据在 unit_bundle.verify_unit_bundle 的 A5 一段）。
        # 搬法：**原样搬字节**（⛔ 不重编码、⛔ 不动 m_TextureFormat / m_MipCount /
        #   m_CompleteImageSize），逐段追加到本包 .resS 尾部，每段起点按 16 字节对齐。
        # ⛔ 搬不动的（源包里也没有那段字节 / 指着别的包 / 路径是空的）**如实报**，不许静默算绿。
        old_node = str(rd.node0.path)
        old_stream = f"{ARCHIVE_PREFIX}{old_node}/{old_node}{'.resS'}"
        src_stream_size = 0 if rd.stream_node is None else int(rd.stream_node.size)
        stream = bytearray(payload)
        moves: list[StreamMove] = []
        stuck: list[StreamStuck] = []
        for pid in seen:
            if int(pid) in edits:
                continue            # 受害者 / 材质：那是上面那两条路管的，别在这儿再动一次
            tree = rd.fields_of(pid)
            sd2 = dict(tree.get("m_StreamData") or {})
            size2 = int(sd2.get("size", 0) or 0)
            if size2 <= 0:
                continue            # 不是流式贴图（内联 / 空）⇒ 不归 A5 这条管
            off2 = int(sd2.get("offset", 0) or 0)
            path2 = str(sd2.get("path") or "")
            name2 = str(tree.get("m_Name") or "")
            if path2 != old_stream:
                stuck.append(StreamStuck(
                    path_id=int(pid), name=name2, path=path2, source_offset=off2, size=size2,
                    reason=("m_StreamData.path 是空的" if not path2 else
                            f"纹素不在源包里（path 指的是 {path2!r}，不是源包自己的 .resS）")))
                continue
            if off2 < 0 or off2 + size2 > src_stream_size:
                stuck.append(StreamStuck(
                    path_id=int(pid), name=name2, path=path2, source_offset=off2, size=size2,
                    reason=(f"源包 .resS 只有 {src_stream_size:,} B，装不下"
                            f" [{off2:,}, {off2 + size2:,})")))
                continue
            blob = rd.stream_bytes(off2, size2)
            stream += b"\0" * ((-len(stream)) % STREAM_APPEND_ALIGN)
            new_off = len(stream)
            stream += blob
            new_tree = dict(tree)
            nsd2 = dict(sd2)
            nsd2["path"] = stream_path
            nsd2["offset"] = int(new_off)
            new_tree["m_StreamData"] = nsd2
            edits[int(pid)] = new_tree
            moves.append(StreamMove(
                path_id=int(pid), name=name2, source_offset=off2, offset=int(new_off),
                size=size2, sha256=hashlib.sha256(blob).hexdigest()))
        moved_offset = {m.path_id: m.offset for m in moves}
        # ---- 逐个把要指的槽改到受害者（只动真的变了的那些；⛔ 空改动不写）----
        swapped_tree = mat_lists
        pointed: list[str] = []
        for name in slots:
            cur = slot_pid_or_none(swapped_tree, name)
            if cur is None:
                if str(slot):
                    raise AssetError(f"槽 {name!r} 不在这个材质里（或 PPtr 是空的）",
                                     hint="槽名不许猜：不给 --slot 时按 shader 的 desc 认")
                continue
            if cur == int(victim_pid):
                pointed.append(name)
                continue
            swapped_tree = skin_install.swap_slot_texture(swapped_tree, name,
                                                          path_id=int(victim_pid))
            pointed.append(name)
        if swapped_tree is not mat_lists:
            edits[int(mat_pid)] = as_pair_dicts(swapped_tree)
        # 槽守卫（⛔ 不绕过；它只读，不改 skin_install）
        for name in pointed:   # 每个要指的槽都过一遍守卫（⛔ 不绕过）
            skin_install.guard_slot_target(_TypeTreeSource(rd), mat_tree, name,
                                           int(victim_pid))

        # ---- 等长 + 非空改动自证 ----
        rows: list[AppliedEdit] = []
        for pid in [q for q in seen if q in edits]:
            tree = edits[pid]
            raw = rd.raw_of(pid)
            blob = rd.encode(pid, tree)
            if blob == raw and not (already and int(pid) == int(victim_pid)):
                raise AssetError(f"pathID={pid} 改完字节一个都没变 —— 空改动不该出包")
            # 变长档：对象长度**可以**变（`.resS` 那条路径串一变长就变）—— 记下前后长度，
            # ⛔ 不再拿"长度相等"当守卫（builder 自己会重排对象表）。
            changed = sum(1 for a, b in zip(raw, blob, strict=False) if a != b)
            changed += abs(len(raw) - len(blob))
            cls = rd.class_of(pid)
            if cls == CLASS_MATERIAL:
                what = "槽 " + "、".join(pointed) + " 指到受害者"
            elif int(pid) == int(victim_pid):
                what = (f"m_StreamData -> 本包 .resS @0 size {len(payload):,} · fmt -> {DXT5}"
                        + ("" if (same_size and int(want_mips) == int(v_mips))
                           else f" · {v_w}x{v_h} mip{v_mips} -> "
                                f"{image_used.width}x{image_used.height} mip{want_mips}"))
            else:
                what = (f"A5 自足化：纹素**原样搬**进本包 .resS"
                        f" @{moved_offset[int(pid)]:,}（fmt / mip / 长度一个都没动）")
            rows.append(AppliedEdit(
                path_id=int(pid), name=str(tree.get("m_Name") or ""),
                type_name="Material" if cls == CLASS_MATERIAL else "Texture2D",
                what=what,
                changed_bytes=changed,
                bytes_before=len(raw), bytes_after=len(blob),
            ))

        edits = {k: v for k, v in edits.items() if k in set(seen)}
        specs = []
        for pid in seen:
            tid = rd.by_pid[pid][4]
            cls = rd.cid.get(tid)
            script_index = None
            if cls == 114:
                si = int(getattr(rd.meta.types[tid], "script_type_index", -1))
                script_index = si if si >= 0 else None
            if pid in edits:
                specs.append(builder.ObjectSpec(
                    class_id=cls, fields=edits[pid], path_id=pid,
                    script_index=script_index, label=f"edited:class{cls}"))
            else:
                specs.append(builder.ObjectSpec(class_id=cls, fields=None, payload=rd.raw_of(pid),
                                                path_id=pid, script_index=script_index,
                                                label=f"payload:class{cls}"))
        needed = sorted({
            int(getattr(rd.meta.types[rd.by_pid[p][4]], "script_type_index", -1))
            for p in seen if rd.cid.get(rd.by_pid[p][4]) == 114
        } - {-1})
        script_type_map = {s: int(rd.meta.script_types[s].local_identifier) for s in needed}
        dangling = builder.dangling_pptrs(
            specs, externals=len(rd.meta.externals), assetbundle_path_id=1,
            script_path_ids=list(script_type_map.values()),
        )
        if dangling:
            raise AssetError(
                f"这个包里出现 {len(dangling)} 条悬空 PPtr（指向包内不存在的 pathID）",
                hint="先跑 unit_bundle.audit_package_pptrs 看明细：⛔ 带悬空的包不许出",
            )
        built = builder.build_object_bundle(
            objects=specs, bundle_name=bundle_name, source_meta=rd.meta_blob,
            script_type_map=script_type_map, container=[(asset_path, int(root))],
            common_strings=rd.common, block_type=builder.BLOCK_LZ4HC,
            preload_all=True, copy_source_tables=True, stream_payload=bytes(stream),
        )
        data = bytes(built.data)
        objects = int(built.object_count)
    return TextureApplyPlan(
        bundle=Path(bundle), out=out_path, bundle_name=bundle_name, node_path=node_path,
        stream_path=stream_path, container=((str(asset_path), int(root)),), root=int(root),
        material_name=str(material), material_path_id=int(mat_pid), slot=str(slot or ""),
        slot_before=int(slot_before), slot_after=int(victim_pid), victim_path_id=int(victim_pid),
        victim_name=v_name, victim_format_before=int(vt.get("m_TextureFormat") or 0),
        victim_stream_before=int(v_size), image_in=image_in, image_used=image_used, note=note,
        alpha_mode=str(alpha), mip_count=int(want_mips), payload=payload, objects=objects,
        edits=tuple(rows), dangling=tuple(dangling), data=data,
        color_layers=tuple(layers), slots_pointed=tuple(pointed), warnings=tuple(warnings),
        victim_width_before=int(v_w), victim_height_before=int(v_h),
        victim_mips_before=int(v_mips),
        stream_moved=tuple(moves), stream_stuck=tuple(stuck), stream_bytes=len(stream),
    )


def apply_texture(**kwargs: Any) -> TextureApplyPlan:
    """`plan_texture_apply` ＋ 落盘到 `--out`（**只写 --out**，⛔ 不碰源包）。

    落盘之后**自己再开一遍出包**，把六个读数与 `.resS` 节点 size、以及 `.resS` 里那一段像素
    逐条对（`verify_texture_out`）—— 任何一条对不上**当场抛**。
    ★ 这一步就是工单 §7 坑 2 的拦网：**只改 `m_TextureFormat`、把长度留旧值**的坏包会死在第 5 条
    （`m_CompleteImageSize != dxt_payload_size(...)`）。
    ★ 同时过一遍 **A5 自足性复核**（`verify_streams_out`）：每张被搬的贴图都要按记录读得回来，
    而且**除了我们已如实报过的 `stream_stuck`**，包里不许再出现别的"纹素落在包外"的贴图。
    """
    plan = plan_texture_apply(**kwargs)
    if plan.out.exists():
        raise AssetError(
            f"落点已经存在：{plan.out}",
            hint="⛔ 不覆盖（v2/v3 那种件是证据）；换一个 --out 名字",
        )
    plan.out.parent.mkdir(parents=True, exist_ok=True)
    plan.out.write_bytes(plan.data)
    if plan.out.stat().st_size != len(plan.data):
        raise AssetError(f"写出来 {plan.out.stat().st_size:,} B != 造出来 {len(plan.data):,} B")
    reading = read_texture_back(plan.out, plan.victim_path_id)
    problems = texture_readback_problems(
        reading,
        expect_width=plan.image_used.width,
        expect_height=plan.image_used.height,
        expect_format=DXT5,
        expect_mip_count=plan.mip_count,
        expect_payload=plan.payload,
        expect_stream_node_size=plan.stream_bytes,
    )
    problems += verify_streams_out(
        plan.out, moved=plan.stream_moved,
        expect_foreign=[s.path_id for s in plan.stream_stuck],
    )
    if problems:
        raise AssetError(
            f"出包复核没过（{len(problems)} 条）：" + "；".join(problems),
            hint="⛔ 这一份不许交付 —— 六个读数里有对不上的，或者有纹素没跟过来"
                 "（最典型的是「只改了格式、长度留旧值」）",
        )
    return replace(plan, readback=reading, problems=problems)


def encode_png_to_payload(png: str | Path, *, mip_count: int | None = None) -> EncodeReport:
    """`texture encode`：PNG → BC3 ＋ 满 mip 的载荷字节（不碰任何包）。"""
    image = read_png(png)
    mips = int(mip_count) if mip_count else max(1, int(image.width).bit_length())
    payload = encode_payload(image, mip_count=mips)
    note = alpha_note(image, gate=0.0, cutoff=0.5, action="n/a")
    return EncodeReport(png=Path(png), image=image, mip_count=mips, payload=payload, note=note)
