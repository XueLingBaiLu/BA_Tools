r"""资产**新增**：一条命令把 PNG 变成游戏里出现的那张图（＋一条命令全撤）。

口径（AGENTS §0.10：产品的原子操作只有**新增**与**删除**）：这条命令 = **一次新增**，
它往游戏里加四样东西，**一个游戏原有的包都不改**：

    ① 一个新 bundle 文件（<游戏根>\BA_Mod\<包名>；落点只有一处规则：:func:`install_landing`）
    ② catalog 一条包登记 ＋ 一条地址（类型 3 / 4 两条 entry，同一个桶），
       并且**同步改写**同目录的 `aa\catalog.hash`（内容 = `hash128_hex(整份 catalog)` 的
       32 个 ASCII 字符、**无换行**）—— 盘上留一份「新 bin + 旧 hash」就是给运行时一个错哈希
    ③ 我们自己的外部库 BA_Mod.zip 里 Units 那一行的 ThumbnailFileName -> 新键
    ④ GameAssembly.dll 的补丁（游戏去读 BA_Mod.zip 的那个开关；本来就打着就不动它）

**撤**全部走现成件（ADR 0007：⛔ 不许拷整包当备份）：
catalog（连同它的 `aa\catalog.hash`）与 BA_Mod.zip 走 `reverse_delta.PatchStore` 的反向 Δ
—— 两份 catalog 文件记在**同一条补丁**里、一起撤；我们加的那个 bundle 是**新文件**，
补丁里记 `before=None` ⇒ 还原就是**删掉它**（`catalog.hash` 装前不存在时同此口径）；
DLL 走 `dllpatch.restore`，
且**只撤本单打的那一次**（见 :func:`restore`）。

## 两条硬知识（都是真机刨出来的，见 kb/14 §8）

1. **Unity 贴图原始数据自下而上**（第 0 行 = 图像底行）⇒ 写 image data 前把行倒序，
   否则游戏里上下镜像。全模块只此一处做这件事：:func:`flip_rows` / :func:`to_unity_image_data`。
2. **catalog 登记的四个字段跟文件名 / 内容的关系**（本机在真件上复算过 ——
   `unitlabels_assets_all_cd24462582b9020da8e27bbbee0749a0.bundle` 那条：
   `m_Hash` = 文件名 `_all_` 尾段 cd24462582b9020da8e27bbbee0749a0（**不是** md5(文件)）、
   `m_Crc` = 474730737 = crc32(解压流 95,456,256 B)、`m_BundleSize` = 16,957,922 = 文件长、
   `m_BundleName` = 包内 AssetBundle `m_Name` 去掉 .bundle）
   ⇒ 这四个值的来源只有一处：:func:`bundle_facts`。

## 干跑

`--dry-run` 下**游戏目录与工程目录一个字节都不写**（连补丁柜都不建）。
唯一会落盘的是系统临时目录里那份待装的 BA_Mod.zip（`external_db.install` 要一个**路径**），
它在同一次调用里就被删掉 —— 报告里那句「一个字节都没写」指的是**游戏目录 / 工程目录**。

## 判据一行（可复算）

`.venv\Scripts\python.exe -m pytest tests/test_asset_add.py`
→ 通过用例数 **从 0（本文件不存在）变 22 passed**（另有一条标了 slow：它只读游戏里 17 MB 的
unitlabels 包造一次真包，`-m slow` 时 +1 passed）；
三条正控（行序不翻就不相等 / 装完手改 1 字节则 restore 拒绝 / Units.json 非我们的口径则拒绝）
见那个文件的模块头。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import tempfile
import time
import zlib
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import builder, clone
from ba_mod_maker.asset_kernel import catalog_edit as ce
from ba_mod_maker.asset_kernel import catalog_writer as cw
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.asset_kernel import reverse_delta as rd
from ba_mod_maker.asset_kernel.container_addr import resolve_container_path
from ba_mod_maker.core.asset import dbzip, dllpatch, external_db, ufs
from ba_mod_maker.core.assets import game as game_mod
from ba_mod_maker.core.assets.catalog_bin import looks_like_catalog_bin
from ba_mod_maker.core.errors import AssetError

# ★ 1.2.1：catalog 换成了二进制（Addressables BinaryStorageBuffer）。判别位只需要 magic，
#   所以这里只 import 判别函数；写侧实现按需在函数内 import（对 1.2.0 那条路是死代码）。

__all__ = [
    "DEFAULT_PPU",
    "FACT_KEYS",
    "KINDS",
    "KIND_RESOURCE_TYPE",
    "MODEL_ASSET_PREFIX",
    "ModelAddPlan",
    "FIT_MODES",
    "LABEL_HEIGHT",
    "LABEL_WIDTH",
    "MANIFEST_NAME",
    "SUBJECT_BOX",
    "AddPlan",
    "AddReport",
    "BundleAddPlan",
    "BundleAddReport",
    "RestoreOutcome",
    "add_catalog_entries",
    "asset_path_of",
    "bundle_facts",
    "bundle_facts_of_file",
    "dependency_hash_from",
    "bundle_file_name",
    "bundle_internal_id",
    "bundle_target_path",
    "install_landing",
    "MOD_DIR_NAME",
    "build_bundle",
    "catalog_hash_bytes",
    "find_unit_row",
    "fit_image",
    "flip_rows",
    "full_rect_sprite_template",
    "manifest_path",
    "model_asset_path_of",
    "plan_add",
    "plan_add_bundle",
    "plan_add_model",
    "read_internal_bundle_name",
    "verify_facts",
    "RowEdit",
    "read_metadata_region",
    "restore",
    "rewrite_zip_row",
    "run_add",
    "run_add_bundle",
    "run_add_bundle_dry",
    "run_add_dry",
    "sha256_of",
    "sprite_fields_for",
    "to_unity_image_data",
    "validate_key",
]

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
#: 目标贴图尺寸 = 真件 label 的尺寸（kb/14 §8：我们造的那张 416x216 真机验过）。
LABEL_WIDTH = 416
LABEL_HEIGHT = 216

#: 主体（用户那张图）等比缩进的正方形框：416x216 里能放下的最大正方形。
SUBJECT_BOX = 216

#: Sprite 的像素/单位（真件实测 100）。
DEFAULT_PPU = 100.0

#: 三种构图。blur 是真机验过的那一版，另两种是同一套口径下的对照。
FIT_MODES = ("blur", "fill", "plain")

#: 模糊背景的两个参数（照抄 _work/addr3_prep2.py，那一版真机显示正常）。
BLUR_RADIUS = 18.0
BLUR_DIM = 0.55

#: 资产路径前缀：容器里的路径 = catalog 的 m_InternalIds 那条（真件是 Assets/...）。
ASSET_PREFIX = "Assets/Mods/BAModMaker/"

#: 模型地址的资产路径前缀（真件形状：真单位模型地址就是 Assets/Resources_moved/ModelPrefabs/…
#: —— 游戏按**键**找地址，前缀可自选；照真件形状是为了以后有人拿眼睛比对时不像假的）。
MODEL_ASSET_PREFIX = "Assets/Resources_moved/ModelPrefabs/"

#: 「新增」这条链的两种 **kind**（AGENTS §0.10：只有新增与删除；模型与图标是同一条链的两种形状）。
#: label = 一张图标（PNG）；model = 一个单位模型（已造好的 UnityFS 包）。
KINDS = ("label", "model")

#: 每种 kind 在 catalog 里的地址形状：label 是 resource_type=3（＋一条类型 4 的别名），
#: model 是 resource_type=1（**单条** entry）。
#: 真件读数（_work/a3-model-address-probe.py）：prefab 地址 6021/6021 都是单条 (1,)；
#: png 地址 12,928 才是 (3,4) 两条。
KIND_RESOURCE_TYPE = {"label": 3, "model": 1}

#: 键的合法形状：字母数字开头，后面字母数字与 . _ -。键会变成**文件名**与**资产名**，
#: 所以在这里挡住路径分隔符 / 空格 / 非 ASCII（挡不住的后果是造出一个游戏不认的名字）。
KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: 类型树的来源包前缀（只读）。拿哪个包不重要，要的是**同一版游戏**的类型树。
META_BUNDLE_PREFIX = "unitlabels"

#: Sprite 形状模板的来源包前缀（只读）：要一条「满幅 + 4 顶点」的真 Sprite。
#: ⚠ 真 label 包里的 Sprite 是**紧网格**（实测 unitlabels：RU_TOROS_S60-Label
#: 21 个顶点、网格 379x166），
#: 所以模板必须另找一家 —— 这一条是 addr2 那批真机验过的组合。
SPRITE_BUNDLE_PREFIX = "ammoicons"

#: 本单的清单（跟补丁柜同一个目录）：记「DLL 是不是本单打的」这类补丁柜装不下的事。
MANIFEST_NAME = "new_asset.json"

#: 待装的那份 BA_Mod.zip 在临时目录里的名字。
WORK_ZIP_NAME = "BA_Mod_new.zip"


# --------------------------------------------------------------------------- #
# ① 图片适配
# --------------------------------------------------------------------------- #
def _open_rgba(path: Path) -> Any:
    """读一张图成 RGBA（错就报 AssetError，别把 PIL 的异常漏到 CLI 上）。"""
    from PIL import Image

    try:
        with Image.open(path) as handle:
            image = handle.convert("RGBA")
    except OSError as exc:
        raise AssetError(
            f"这张图读不出来：{path}（{exc}）",
            hint="给一张 PNG / JPG；文件坏了或后缀名与内容不符都会走到这里。",
        ) from exc
    if image.width < 1 or image.height < 1:
        raise AssetError(f"这张图是空的：{path}（{image.width}x{image.height}）")
    return image


def _subject(image: Any, box: int) -> Any:
    """用户那张图**等比**缩进 box x box 的正方形（不裁、不变形）。"""
    from PIL import Image

    scale = min(box / image.width, box / image.height)
    return image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.LANCZOS,
    )


def _fill(image: Any, width: int, height: int) -> Any:
    """居中裁切到目标比例再整体缩放（会裁掉上下或左右）。"""
    from PIL import Image

    target = width / height
    if image.width / image.height > target:
        keep = max(1, round(image.height * target))
        left = (image.width - keep) // 2
        box = (left, 0, left + keep, image.height)
    else:
        keep = max(1, round(image.width / target))
        top = (image.height - keep) // 2
        box = (0, top, image.width, top + keep)
    return image.crop(box).resize((width, height), Image.LANCZOS)


def fit_image(
    source: str | Path,
    *,
    fit: str = "blur",
    width: int = LABEL_WIDTH,
    height: int = LABEL_HEIGHT,
) -> bytes:
    """把用户的图调成 width x height 的 RGBA（**自上而下** = 游戏会看到的方向）。

    | --fit | 主体 | 两侧 / 空白 |
    |---|---|---|
    | blur（默认） | 等比缩进 216x216 的方框、居中 | 整图缩到 416x216 ＋ 高斯模糊 ＋ 压暗到 55% |
    | fill | **居中裁切**到 416:216 再整体缩放（会裁掉上下或左右） | 没有空白，铺满 |
    | plain | 同 blur 的主体 | 全透明 |

    ⚠ blur 那一档是**真机验过的那一版**（kb/14 §8：BMD-2 那格出现的就是它），
    所以它的每一步都照抄 _work/addr3_prep2.py，不许「顺手改好一点」。
    """
    from PIL import Image, ImageFilter

    if fit not in FIT_MODES:
        raise AssetError(f"不认得的构图 {fit!r}", hint=f"只能是 {' / '.join(FIT_MODES)}")
    path = Path(source)
    if not path.is_file():
        raise AssetError(f"找不到图片：{path}", hint="--png 要给一个存在的图（PNG / JPG）")
    image = _open_rgba(path)
    if fit == "fill":
        return _fill(image, width, height).tobytes()

    background_alpha = 0 if fit == "plain" else 255
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, background_alpha))
    if fit == "blur":
        blurred = (
            image.resize((width, height), Image.LANCZOS)
            .filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
            .point(lambda value: int(value * BLUR_DIM))
        )
        canvas.paste(blurred.convert("RGBA"), (0, 0))
    inner = _subject(image, SUBJECT_BOX)
    canvas.paste(inner, ((width - inner.width) // 2, (height - inner.height) // 2))
    return canvas.tobytes()


# --------------------------------------------------------------------------- #
# ② 行序（Unity 贴图自下而上）
# --------------------------------------------------------------------------- #
def flip_rows(buf: bytes, width: int, height: int) -> bytes:
    """把 RGBA 缓冲的**行序**倒过来（自逆：翻两次回到原样）。

    Unity 的贴图原始数据第 0 行 = 图像**底**行（kb/14 §8 硬知识 1，独立尺子 27/27 命中）。
    写进包之前要翻一次；按「游戏看到的方向」核对时也要翻一次。
    """
    stride = width * 4
    if len(buf) != stride * height:
        raise AssetError(
            f"RGBA 缓冲长度 {len(buf):,} != {stride * height:,}（{width}x{height}）",
            hint="行长与像素数对不上，先查是从哪来的这份缓冲。",
        )
    return b"".join(buf[y * stride : (y + 1) * stride] for y in range(height - 1, -1, -1))


def to_unity_image_data(rgba: bytes, width: int, height: int) -> bytes:
    """源图（自上而下）-> 包内 image data（自下而上）。"""
    return flip_rows(rgba, width, height)


# --------------------------------------------------------------------------- #
# ③ 造包（类型树从游戏真包读，⛔ 不抄任何 blob 进代码）
# --------------------------------------------------------------------------- #
def _pick_bundle(bundle_dir: Path, prefix: str) -> Path:
    hits = sorted(bundle_dir.glob(f"{prefix}*.bundle"))
    if not hits:
        raise AssetError(
            f"游戏目录里找不到 {prefix}*.bundle（造包要照抄它的类型树 / Sprite 形状）",
            hint="--game 指游戏安装目录；确认 BrokenArrow_Data\\StreamingAssets\\aa\\PC 里有包。",
        )
    return hits[0]


def read_metadata_region(bundle: str | Path) -> bytes:
    """真包的**元数据区**（48 B 头 + 类型表）：造包时逐字节照抄类型树。

    ⛔ 不许把这份 blob 抄进代码或仓库（61 KB 级、且换游戏版本就过期）—— 每次现读。
    """
    with ufs.UfsReader(bundle) as reader:
        node = reader.nodes[0]
        head = bytes(reader.read_range(node.offset, builder.SF_HEADER_SIZE))
        size = int.from_bytes(head[20:24], "big")
        return bytes(reader.read_range(node.offset, builder.SF_HEADER_SIZE + size))


def full_rect_sprite_template(bundle: str | Path) -> tuple[dict[str, Any], int]:
    """从真包里取一条**满幅 + 4 顶点**的 Sprite 字段树当形状模板（连它的 pathID 一起给）。"""
    import UnityPy

    env = UnityPy.load(str(bundle))
    for obj in env.objects:
        if obj.type.name != "Sprite":
            continue
        tree = obj.read_typetree()
        vertex_data = tree["m_RD"]["m_VertexData"]
        if vertex_data["m_VertexCount"] != 4 or len(tree["m_RD"]["m_IndexBuffer"]) != 12:
            continue
        points = [struct.unpack_from("<3f", vertex_data["m_DataSize"], 12 * i) for i in range(4)]
        ppu = tree["m_PixelsToUnits"] or DEFAULT_PPU
        span_x = (max(p[0] for p in points) - min(p[0] for p in points)) * ppu
        span_y = (max(p[1] for p in points) - min(p[1] for p in points)) * ppu
        rect = tree["m_Rect"]
        if abs(span_x - rect["width"]) < 1.5 and abs(span_y - rect["height"]) < 1.5:
            return tree, int(obj.path_id)
    raise AssetError(
        f"{Path(bundle).name} 里没有「满幅 4 顶点」的 Sprite 可当形状模板",
        hint=f"默认拿 {SPRITE_BUNDLE_PREFIX}*.bundle；那条真件实测就是满幅 4 顶点。",
    )


def sprite_fields_for(
    template: dict[str, Any],
    *,
    name: str,
    width: int,
    height: int,
    asset_path: str,
    ppu: float = DEFAULT_PPU,
) -> dict[str, Any]:
    r"""真·满幅 Sprite 的字段树 => 我们这张图的满幅 Sprite（口径全部实测）。

    * uvTransform = (ppu, 宽/2, ppu, 高/2) —— 真件四条全命中（72x12 / 288x56 / 800x400 / 416x216）；
    * settingsRaw = 64、atlasRectOffset = (-1,-1) —— 查过的真 Sprite 全一样；
    * 顶点 = 矩形四角（像素 / ppu），三角形 = [0,1,2, 2,1,3]（照抄真件那条 72x12）。
    """
    import copy

    fields = copy.deepcopy(dict(template))
    fields["m_Name"] = name
    fields["m_Rect"] = {"x": 0.0, "y": 0.0, "width": float(width), "height": float(height)}
    fields["m_Offset"] = {"x": 0.0, "y": 0.0}
    fields["m_Border"] = {"x": 0.0, "y": 0.0, "z": 0.0, "w": 0.0}
    fields["m_Pivot"] = {"x": 0.5, "y": 0.5}
    fields["m_PixelsToUnits"] = float(ppu)
    fields["m_IsPolygon"] = False
    fields["m_AtlasTags"] = []
    fields["m_SpriteAtlas"] = {"m_FileID": 0, "m_PathID": 0}
    fields["m_Bones"] = []
    fields["m_PhysicsShape"] = []
    digest = hashlib.md5(asset_path.encode("utf-8")).digest()
    fields["m_RenderDataKey"] = {
        "first": {f"data[{i}]": struct.unpack_from("<I", digest, 4 * i)[0] for i in range(4)},
        "second": 21300000,
    }
    render = fields["m_RD"]
    render["textureRect"] = {"x": 0.0, "y": 0.0, "width": float(width), "height": float(height)}
    render["textureRectOffset"] = {"x": 0.0, "y": 0.0}
    render["atlasRectOffset"] = {"x": -1.0, "y": -1.0}
    render["settingsRaw"] = 64
    render["uvTransform"] = {"x": float(ppu), "y": width / 2.0, "z": float(ppu), "w": height / 2.0}
    render["downscaleMultiplier"] = 1.0
    render["alphaTexture"] = {"m_FileID": 0, "m_PathID": 0}
    render["secondaryTextures"] = []
    render["m_Bindpose"] = []
    half_w, half_h = width / 2.0 / ppu, height / 2.0 / ppu
    corners = [
        (-half_w, half_h, 0.0),
        (half_w, half_h, 0.0),
        (-half_w, -half_h, 0.0),
        (half_w, -half_h, 0.0),
    ]
    payload = b"".join(struct.pack("<3f", *corner) for corner in corners)
    payload += b"\x00" * ((-len(payload)) % 16)  # 位置流按 16 对齐（实测口径）
    payload += b"\x00" * (8 * 4)  # UV 流：真件这一截**全是 0**（实测）
    vertex_data = dict(render["m_VertexData"])
    vertex_data["m_VertexCount"] = 4
    vertex_data["m_DataSize"] = payload
    render["m_VertexData"] = vertex_data
    render["m_IndexBuffer"] = [0, 0, 1, 0, 2, 0, 2, 0, 1, 0, 3, 0]  # u16 [0,1,2,2,1,3]
    submesh = copy.deepcopy(render["m_SubMeshes"][0])
    submesh["indexCount"] = 6
    submesh["vertexCount"] = 4
    submesh["firstByte"] = 0
    submesh["baseVertex"] = 0
    submesh["firstVertex"] = 0
    render["m_SubMeshes"] = [submesh]
    return fields


def validate_key(key: str) -> str:
    """键的合法形状。它会变成**文件名**与**资产名**，所以在这里 fail-closed。"""
    text = str(key)
    if not KEY_PATTERN.match(text):
        raise AssetError(
            f"这个键不能当资产名：{text!r}",
            hint="字母数字开头，后面只许字母数字与 . _ -（例 MY_BMD2-Label）；"
            "它会被拼进 aa\\PC\\<键>_assets_all_<16hex>.bundle。",
        )
    return text


def asset_path_of(key: str, *, suffix: str = ".png", prefix: str = ASSET_PREFIX) -> str:
    """容器里的资产路径 = catalog 的 m_InternalIds 那条（真件形状 Assets/...）。

    默认就是图标线那一档（`.png` + `ASSET_PREFIX`）—— 改前改后逐字节相同；
    模型线用 :func:`model_asset_path_of`（`.prefab` + 真件形状的前缀）。
    """
    return f"{prefix}{validate_key(key)}{suffix}"


def model_asset_path_of(key: str) -> str:
    """模型地址的资产路径（真件形状，例 Assets/Resources_moved/ModelPrefabs/RouteA/X.prefab）。"""
    return asset_path_of(key, suffix=".prefab", prefix=MODEL_ASSET_PREFIX)


def bundle_file_name(key: str) -> str:
    """由键派生的**确定性**包名：<slug>_assets_all_<16hex>.bundle。

    16hex 从 <资产路径>|<键>|v1 的 sha256 前 16 位来 —— 与 addr2 那批真机验过的命名同形
    （ourlabels_assets_all_24d7ddb3de2daf8c.bundle）。同一个键 ⇒ 同一个包名 ⇒
    第二次跑会**撞落点**（那是故意的：要重做先 restore）。
    """
    text = validate_key(key)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower() or "asset"
    digest = hashlib.sha256(f"{asset_path_of(text)}|{text}|v1".encode()).hexdigest()[:16]
    return f"{slug}_assets_all_{digest}.bundle"


#: 我们自己装的包在游戏根下的**唯一落点目录**（2026-10-19 真机验过的那一套：
#: 包只落 `<游戏>\BA_Mod\`，`aa\PC` 保持原版）。
#: ⛔ 这是**唯一一处**：三条装机路径（`newasset add-model` / `newasset add-bundle` /
#: `pack install`）全部经 :func:`install_landing`，别再各写一份。
MOD_DIR_NAME = "BA_Mod"

#: 真件 catalog 里包地址的「平台目录」段（`{…RuntimePath}\PC\<包>`；kb/02 §4 实测）。
PC_SEGMENT = "\\PC\\"

#: 把上面那一段换成它 ⇒ 从 `aa` 往上三级正好是游戏根，再落进 `BA_Mod\`。
MOD_SEGMENT = "\\..\\..\\..\\BA_Mod\\"


def install_landing(game_root: str | Path) -> Path:
    """**唯一落点**：`<游戏根>\\BA_Mod`。

    为什么这一个目录（不是 `aa\\PC`）：2026-10-19 真机验过「包只落 `BA_Mod\\`、`aa\\PC` 保持原版」
    那一套（catalog 里写 `..\\..\\..\\BA_Mod\\<包>`）—— 我们自己的东西与游戏原版包分开，
    Steam 校验 / 游戏更新只碰它自己的目录。
    """
    return Path(game_root) / MOD_DIR_NAME


def bundle_internal_id(native_internal_id: str, bundle_name: str) -> str:
    """真件形状的包 internalId -> **我们落点**的 internalId（与 `install_landing` 同一条规则）。

    输入 = 真件 catalog 里包条目的形状 `{…RuntimePath}\\PC\\<包名>`
    （kb/02 §4 实测；⛔ 别按 `/` 拼）。
    输出 = `{…RuntimePath}\\..\\..\\..\\BA_Mod\\<包名>` —— 从 `aa` 往上三级 = 游戏根，
    正好是 `install_landing()` 那个目录。**包落哪儿、catalog 指哪儿必须一起改**（`game.py`
    的 `resolve_bundle_path` 就是按这个两级相对形状解析回盘上的）。

    ⛔ fail-closed：真件形状变了（没有 `\\PC\\` 段 / 不以包名收尾）就报 —— 静默留着原形状会
    写出「包落在 `BA_Mod`、catalog 指着 `aa\\PC`」这种自相矛盾的现场，症状只在真机上。
    """
    text = str(bundle_name)
    native = str(native_internal_id)
    if not native.endswith(text) or PC_SEGMENT not in native:
        raise AssetError(
            f"真件包地址不是 `...\\PC\\<包名>` 的形状，落点改写没有依据：{native!r}",
            hint="包落在 <游戏根>\\BA_Mod\\ ⇒ catalog 那一条必须指同一处"
                 "（..\\..\\..\\BA_Mod\\）；这条形状是从真件 catalog 的包条目上抄的，"
                 "抄不到就别装。",
        )
    return native.replace(PC_SEGMENT, MOD_SEGMENT, 1)


def bundle_target_path(game_root: str | Path, bundle_name: str) -> Path:
    """新包在游戏里的落点 = :func:`install_landing` 下（**唯一规则**，⛔ 别在这里另立一处）。"""
    return install_landing(game_root) / bundle_name


def build_bundle(
    *,
    key: str,
    image_display: bytes,
    meta_bundle: str | Path,
    sprite_bundle: str | Path,
    width: int = LABEL_WIDTH,
    height: int = LABEL_HEIGHT,
    block_type: int = builder.BLOCK_LZ4HC,
) -> builder.MinBundlePlan:
    """造包：Texture2D（内联 RGBA32）+ Sprite（满幅 4 顶点）+ AssetBundle。

    image_display 是**游戏会看到的方向**（= 源 PNG 的正序）；本函数负责翻成自下而上。
    """
    meta = read_metadata_region(meta_bundle)
    template, _template_path_id = full_rect_sprite_template(sprite_bundle)
    fields = sprite_fields_for(
        template, name=key, width=width, height=height, asset_path=asset_path_of(key)
    )
    return builder.build_texture_bundle(
        asset_path=asset_path_of(key),
        texture=builder.Texture2DData(
            name=key,
            width=width,
            height=height,
            image=to_unity_image_data(image_display, width, height),
        ),
        bundle_name=bundle_file_name(key),
        source_meta=meta,
        common_strings=clone.unitypy_common_strings(),
        sprite_template=fields,
        block_type=block_type,
    )


#: catalog 四件套里与**文件本身**绑定的三个键（`internal_name` 是包内真值，见下）。
FACT_KEYS = ("bundle_name", "m_hash", "crc", "bundle_size", "internal_name")


def read_internal_bundle_name(bundle: str | Path) -> str | None:
    """包内 AssetBundle(142) 对象的 `m_Name` —— catalog 的 `m_BundleName` 的**真值**来源。

    ⚠ 2026-10-08 实测（`_work/model-add/probe-internal-name.py`）：真值**带** `.bundle` 后缀，
    而 `builder.internal_bundle_name_of(文件名)` 是**去掉**后缀那一档 —— 两者对**我们的包**
    差一个后缀、对真件差得更多（units 的真值是 `b54a3ecae2424f30d00f4dbb79449716.bundle`，
    跟文件名毫无关系）。所以模型线用**读出来的真值**；图标线保持原来的名字派生口径不动
    （它真机验过，零回归优先）。

    只用产品自己的读器（不开 UnityPy）—— 与 UnityPy 读出来的是同一串（探针里对拍过）。
    """
    path = Path(bundle)
    with ufs.UfsReader(path) as reader:
        nodes = [n for n in reader.nodes if not n.path.endswith(".resS")]
        if not nodes:
            return None
        node = nodes[0]
        header = mw.parse_header(reader.read_range(node.offset, builder.SF_HEADER_SIZE))
        meta = mw.parse_serialized_metadata(
            reader.read_range(node.offset, header.data_offset)
        )
        type_ids = {
            i for i, item in enumerate(meta.types) if item.class_id == builder.CLASS_ASSETBUNDLE
        }
        if not type_ids:
            return None
        entry = next((o for o in meta.objects if o.type_id in type_ids), None)
        if entry is None:
            return None
        tree = meta.types[entry.type_id].type_tree
        if tree is None:
            return None
        node_list = clone.read_type_nodes(
            mw.encode_type_tree(tree, "<"), common_strings=clone.unitypy_common_strings()
        )
        start = node.offset + header.data_offset + entry.byte_start
        fields = clone.read_object(reader.read_range(start, entry.byte_size), node_list)
    text = fields.get("m_Name")
    return None if text is None else str(text)


def bundle_facts_of_file(bundle: str | Path) -> tuple[dict[str, Any], bytes, int]:
    """读**盘上那个已造好的包**，出 catalog 四件套 ＋ 包字节 ＋ 解压流字节数。

    三件与 :func:`bundle_facts` 同口径（m_Hash = 文件名 _all_ 尾段、m_Crc = crc32(解压流)、
    m_BundleSize = 文件长）；`internal_name` 取**包内真值**（见 read_internal_bundle_name）。
    """
    path = Path(bundle)
    if not path.is_file():
        raise AssetError(f"找不到要新增的包：{path}", hint="--bundle 指一个已造好的 UnityFS 包")
    data = path.read_bytes()
    with ufs.UfsReader(path) as reader:
        serialized_size = int(reader.total_uncompressed)
        crc = zlib.crc32(reader.read_range(0, serialized_size)) & 0xFFFFFFFF
    facts = bundle_facts(path.name, data, crc)
    # ⚠️ 这里**盖掉** bundle_facts 的名字派生口径，换成包内真值 —— 这是模型那一档（同上裁定）：
    #   · 口径 = 包内 AssetBundle(142).m_Name 的**真值**；实测**带** ".bundle" 后缀：
    #     我们的包读到 `routebunit_assets_all_5847a22c37556b4a.bundle`，
    #     真件 units 读到 `b54a3ecae2424f30d00f4dbb79449716.bundle`
    #     （32-hex 不透明名，与文件名毫无关系）⇒ 只有**读包**才诚实；
    #   · 图标线**不采用**这一条（它真机验过，见 bundle_facts 那段注释）；
    #   · 复算：_work/model-add/probe-internal-name.py（三条读法对拍，产品读器 == UnityPy）；
    #     与 routeb_install.py 的逐字段对拍见 _work/model-add/cross-check.py；
    #   · 读不出来（不是 SerializedFile / 包里没有 AssetBundle 对象）⇒ 保持派生名，不静默报错。
    internal = read_internal_bundle_name(path)
    if internal:
        facts["internal_name"] = internal
    return facts, data, serialized_size


def verify_facts(facts: Mapping[str, Any], actual: Mapping[str, Any]) -> None:
    """把**外面给的一份 facts**（例：装配那一步写下的 facts.json）与文件实际读数对账。

    fail-closed：对不上就抛（⛔ 不静默采信）。理由是真的：catalog 登记的 m_Crc / m_BundleSize
    是游戏加载这个包的依据 —— 包在装配之后被改过而 facts 没更新，登记进 catalog 就是
    一个**加载不上的地址**（症状出现在真机上，且很难往这里想）。
    """
    missing = [name for name in FACT_KEYS if name not in facts]
    if missing:
        raise AssetError(
            f"给的 facts 里缺 {missing}",
            hint=f"至少要 {list(FACT_KEYS)}（由 bundle_facts 产出）",
        )
    bad = [(name, facts[name], actual[name]) for name in FACT_KEYS if facts[name] != actual[name]]
    if bad:
        lines = "；".join(
            f"{name}: 给的是 {given!r}，文件实际是 {real!r}" for name, given, real in bad
        )
        raise AssetError(
            f"facts 与文件对不上（{len(bad)} 项）：{lines}",
            hint="facts 必须是**这个包**装配完那一刻的四件套；包改过就重算一遍再登记",
        )

def bundle_facts(bundle_name: str, data: bytes, crc32_stream: int) -> dict[str, Any]:
    """catalog 登记要的四个值 —— **唯一**的来源（口径见模块头第二段）。

    m_Hash 不是 md5(文件)：真件口径 = **文件名 _all_ 后面那一段**。
    """
    tail = bundle_name[: -len(".bundle")].rsplit("_all_", maxsplit=1)[-1]
    return {
        "bundle_name": bundle_name,
        "m_hash": tail,
        "crc": int(crc32_stream),
        "bundle_size": len(data),
        # ⚠️ m_BundleName 有**两条口径**，本产品**故意不统一**（Lead 2026-10-08 裁定）。
        #    这是图标那一档：
        #   · 口径 = **名字派生**：internal_bundle_name_of(文件名) = 去掉 ".bundle" 后缀；
        #   · 为什么不改：这一档**真机验过**
        #     （docs/交接-2026-10-06-删除与收缩.md:45：IoU 0.803 / NCC 0.669），
        #     而这个字段**不是加载判据** —— 真件 m_BundleName 与"文件名去 .bundle"同形 **0/77**
        #     （_work/a3-assembly-gaps.md D8）；
        #   · 另一档在 :func:`bundle_facts_of_file`
        #     （模型线 = 包内 m_Name 的**真值**，带 ".bundle"）。
        #   ⛔ 别为了"看起来一致"来改这一行：真机验过的一档，改了要重验一轮、收益为零。
        "internal_name": builder.internal_bundle_name_of(bundle_name),
    }


# --------------------------------------------------------------------------- #
# ④ catalog：包登记 + 地址（类型 3 / 4 两条 entry，同一个桶）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Registration:
    """新增进 catalog 的那几条的下标 + 计数头前后。"""

    key_index: int
    bundle_key_index: int
    bundle_entry_index: int
    asset_entry_index: int
    alias_entry_index: int
    internal_id_index: int
    counts_before: tuple[int, int, int]
    counts_after: tuple[int, int, int]
    #: "label" / "model"（见 KINDS）—— 地址形状不同，报告文案也不同。
    kind: str = "label"
    #: 模型线的依赖组键下标（label 线没有组，是 -1）。
    dependency_group_index: int = -1
    #: 模型线的依赖 hash（抄自 source_key 那条真件 prefab 地址的 entry[3]）。
    dependency_hash: int = 0
    dependency_source_key: str = ""
    #: 那条地址 entry 的**原样 7 格**（比对形状用；真件模型地址是 (1,) 单条、(3,4) 才是图标）。
    address_row: tuple[int, ...] = ()
    #: 这个包在 catalog 里的 internalId —— 与 :func:`install_landing` 指**同一处**（落点唯一规则）。
    internal_id: str = ""

    def summary(self) -> str:
        before, after = self.counts_before, self.counts_after
        head = (
            f"计数头 {before[0]:,} / {before[1]:,} / {before[2]:,}"
            f" -> {after[0]:,} / {after[1]:,} / {after[2]:,}"
        )
        if self.kind == "model":
            return (
                f"{head}（键 {self.bundle_key_index} 包登记、"
                f"键 {self.dependency_group_index} 依赖组、键 {self.key_index} 地址；"
                f"条目 #{self.bundle_entry_index}"
                f" + #{self.asset_entry_index}）"
            )
        return (
            f"{head}（键 {self.bundle_key_index} 包登记、键 {self.key_index} 地址；"
            f"条目 #{self.bundle_entry_index} + #{self.asset_entry_index}"
            f" + #{self.alias_entry_index}）"
        )


def dependency_hash_from(edit: ce.CatalogEdit, source_key: str) -> int:
    """从 `source_key` 那条**真件模型地址**上抄依赖 hash（entry[3]）。

    口径与 _work/route-b/routeb_install.py 逐字相同：在那个键的桶里找「primaryKey == 自己、
    且 entry[2] != -1」的那条 entry，取它的 entry[3]。三条 fail-closed：

    1. 键不在 catalog ⇒ 抛（⛔ 不许瞎编一个 hash）；
    2. 那条 entry 不是模型地址（`resource_type != 1`）⇒ 抛；
    3. entry[3] 是空哈希 ⇒ 抛。

    真件读数：`RU_T72B` 那条地址的 entry[3] = **1392117474**
    （复算：_work/model-add/cross-check.py）。
    """
    index = edit.key_index_of(source_key)
    if index is None:
        raise AssetError(
            f"catalog 里没有依赖来源键 {source_key!r}",
            hint="模型地址要抄一条真件 prefab 地址的依赖 hash；换一个存在的键（例 RU_T72B）",
        )
    for entry_index in edit.buckets[index]:
        row = edit.entries[int(entry_index)]
        if row[ce.ENTRY_PRIMARY_KEY] != index or row[ce.ENTRY_DEPENDENCY_KEY] == -1:
            continue
        if int(row[ce.ENTRY_RESOURCE_TYPE]) != KIND_RESOURCE_TYPE["model"]:
            raise AssetError(
                f"{source_key!r} 那条 entry 的 resource_type="
                f"{int(row[ce.ENTRY_RESOURCE_TYPE])} != 1 —— 它不是模型地址",
                hint="依赖 hash 只从 prefab 地址（resource_type=1）上抄；别从图标地址上抄",
            )
        value = int(row[ce.ENTRY_DEPENDENCY_HASH])
        if value == cw.INVALID_DEPENDENCY_HASH:
            raise AssetError(
                f"{source_key!r} 那条地址的 entry[3] 是空哈希（{value}）",
                hint="换一条真有依赖组的真件 prefab 地址",
            )
        return value
    raise AssetError(
        f"{source_key!r} 的桶里没有带依赖键的地址 entry",
        hint="真件模型地址的形状 = resource_type=1 且 entry[2]（依赖键）!= -1",
    )


def _repoint_registered_bundle(
    raw: bytes, *, entry_index: int, bundle_name: str
) -> tuple[bytes, str]:
    """把**刚登记那一条**包条目的 internalId 改写到我们的落点（纯函数，⛔ 不碰盘）。

    `catalog_bin_write.add_bundle` 写的是**真件形状**（`…\\PC\\<包>`，运行时前缀抄真件包条目）；
    而包实际落在 :func:`install_landing`（`<游戏根>\\BA_Mod\\`）⇒ 这一条必须跟着改成
    `…\\..\\..\\..\\BA_Mod\\<包>`，否则 catalog 指着 `aa\\PC`、盘上却在 `BA_Mod`（游戏取不到）。

    两条自证（读回，不认内存里的值）：① 计数头不许变；② 重编码之后读回来那条必须是新值。
    为什么放在这里而不是 `catalog_bin_write.add_bundle` 里：cbw 只管**格式**，落点是产品口径
    （唯一一处 = :func:`install_landing`）；而且这一步是纯函数 ⇒ 补丁柜只记**最终**字节，
    旧 `pack install` 那笔「repoint 之后要重记柜子」的账不再存在。
    """
    from ba_mod_maker.core.assets import catalog_bin_write as cbw

    model = cbw.read_model(bytes(raw))
    index = int(entry_index)
    entry = model.entries[index]
    internal_id = bundle_internal_id(str(entry.internal_id or ""), bundle_name)
    entries = list(model.entries)
    entries[index] = replace(entry, internal_id=internal_id)
    after = cbw.serialize(replace(model, entries=entries))
    if cbw.counts(raw) != cbw.counts(after):
        raise AssetError(
            "改写包 internalId 之后 catalog 计数变了（不该发生）",
            hint="这是纯函数那一步的自检；红了先别装 —— 说明写侧把条目结构动了。",
        )
    reread = cbw.read_model(after).entries[index].internal_id or ""
    if reread != internal_id:
        raise AssetError(
            f"改写包 internalId 之后读回来不是新值：{reread!r} != {internal_id!r}",
            hint="读回自证不过 ⇒ catalog 里的包路径与实际落点对不上，别装。",
        )
    return after, internal_id


def _add_catalog_entries_bin(
    catalog_before: bytes,
    *,
    key: str,
    asset_path: str,
    facts: dict[str, Any],
    kind: str,
    resource_type: int | None,
    dependency_source_key: str | None,
    dependency_policy: str = "auto",
) -> tuple[bytes, Registration]:
    """1.2.1 的**二进制** catalog 走这条：包登记 ＋ 一条地址。

    输出契约与 JSON 那条路**相同**（`(新字节, Registration)`），只是两步都走 bin 后端。

    为什么单独一条：1.2.1 把 catalog 从 JSON 明文换成了二进制（magic `0x0DE38942`）⇒
    `CatalogEdit.from_bytes` 的 `json.loads` 会 `UnicodeDecodeError 0x89`。写侧实现住
    `core/assets/catalog_bin_write.py`（它的正控是**逐字节复刻真件**：重建 sha256 == 真件 sha256）。

    ⚠ 与 JSON 那条路的**语义差异**（都是实测得来的，别照 JSON 的口径想当然）：
    * 二进制里**没有桶**、KeyData 顺序无关 ⇒ 追加即可，不必排序、不必修桶；
    * `dependencyHashValue` 真件 17,963 条**全是 0** ⇒ 不用复刻任何 hash 算法；
    * 依赖声明是 entry 内联的**包条目数组**（不是 JSON 那种「依赖组键对象」）⇒
      **没有 dependency_group 这个概念**，所以 `dependency_group_index` 恒为 -1；
    * 普通地址与包条目的两个字段**方向相反**：
      普通地址 `extra=0xFFFFFFFF`、`depSet` 有效；包条目 `depSet=0xFFFFFFFF`、`extra` 有效 ⇒
      写反的后果是新地址**声明零依赖**（跨包依赖加载不到）。
    """
    # 函数内 import：对 1.2.0 那条路是死代码，别让 JSON 侧付它的 import 代价。
    from ba_mod_maker.core.assets import catalog_bin_write as cbw

    want_type = KIND_RESOURCE_TYPE[kind] if resource_type is None else int(resource_type)
    bundle_plan = cbw.add_bundle(
        catalog_before,
        bundle_name=facts["bundle_name"],
        m_hash=facts["m_hash"],
        crc=int(facts["crc"]),
        bundle_size=int(facts["bundle_size"]),
        internal_name=facts["internal_name"],
    )
    # ★ 落点（唯一规则 = new_asset.install_landing）：包落 `<游戏>\BA_Mod\`，catalog 那一条
    #   必须指同一处 ⇒ 把刚登记那条包条目改写成 `..\..\..\BA_Mod\` 形状。
    registered, internal_id = _repoint_registered_bundle(
        bundle_plan.after,
        entry_index=int(bundle_plan.bundle_entry_index),
        bundle_name=facts["bundle_name"],
    )
    addr_plan = cbw.add_address(
        registered,
        key=key,
        asset_path=asset_path,
        bundle_name=facts["bundle_name"],
        resource_type=want_type,
        dependency_source_key=(str(dependency_source_key) if dependency_source_key else None),
        dependency_policy=dependency_policy,
    )
    return addr_plan.after, Registration(
        # 二进制里键是**对象**，没有「键下标」这个数 ⇒ -1（读 `Registration.summary` 的人要知情）。
        key_index=-1,
        bundle_key_index=int(bundle_plan.bundle_key_index),
        bundle_entry_index=int(bundle_plan.bundle_entry_index),
        asset_entry_index=int(addr_plan.entry_index),
        # 图标线那「两条 entry 共一个桶」是 JSON 口径，二进制侧无对应物。
        alias_entry_index=-1,
        internal_id_index=int(addr_plan.internal_id_index),
        counts_before=tuple(bundle_plan.counts_before),
        counts_after=tuple(addr_plan.counts_after),
        kind=kind,
        dependency_group_index=-1,
        dependency_hash=int(addr_plan.dependency_hash or 0),
        dependency_source_key=str(dependency_source_key or ""),
        address_row=(),
        internal_id=internal_id,
    )


def add_catalog_entries(
    catalog_before: bytes,
    *,
    key: str,
    asset_path: str,
    facts: dict[str, Any],
    kind: str = "label",
    resource_type: int | None = None,
    dependency_source_key: str | None = None,
    dependency_policy: str = "own-only",
) -> tuple[bytes, Registration]:
    """往 catalog 里**新增**一条包登记 ＋ 一条地址（形状按 `kind` 分派）。返回（新字节，读数）。

    * `label`（默认，图标线）：包登记 ＋ **类型 3 / 4 两条 entry 共一个桶**，依赖键 = 包自己那条键。
      真件读数（_work/a3-model-address-probe.py）：png 地址 12,928 条是 (3,4)。
    * `model`（模型线）：包登记 ＋ **一条真件形状的依赖组键** ＋ 一条 `resource_type=1` 的地址，
      依赖 hash **抄**一条真件 prefab 地址的 entry[3]。
      真件读数：prefab 地址 6021/6021 都是单条 (1,)。

    三条 fail-closed（都有用例）：

    1. 键 / 包名已经在 catalog 里 ⇒ 抛（入库的键不许覆盖）；
    2. `resource_type` 给了但与 kind 的形状不符 ⇒ 抛；
    3. 模型线：依赖来源键不存在 / 不是模型地址 / 依赖 hash 是空的 ⇒ 抛（见 dependency_hash_from）。
    """
    if kind not in KINDS:
        raise AssetError(f"不认得的 kind：{kind!r}", hint=f"只能是 {" / ".join(KINDS)}")
    want_type = KIND_RESOURCE_TYPE[kind]
    if resource_type is not None and int(resource_type) != want_type:
        raise AssetError(
            f"{kind} 的地址 resource_type 必须是 {want_type}，拿到 {resource_type}",
            hint="真件：prefab 地址 6021/6021 都是 resource_type=1 单条；png 地址才是 (3,4)。",
        )
    # ★ 1.2.1 起 catalog 是二进制 ⇒ 走 bin 后端。
    #   （上面两条校验**两条路都要走**，所以分流放在它们之后。）
    if looks_like_catalog_bin(catalog_before):
        return _add_catalog_entries_bin(
            catalog_before,
            key=key,
            asset_path=asset_path,
            facts=facts,
            kind=kind,
            resource_type=resource_type,
            dependency_source_key=dependency_source_key,
            dependency_policy=dependency_policy,
        )
    edit = ce.CatalogEdit.from_bytes(catalog_before)
    counts_before = edit.counts
    if edit.key_index_of(key) is not None:
        raise AssetError(
            f"catalog 里已经有这个键：{key!r}",
            hint="换一个 --key，或先 newasset restore 把上一次的撤掉（入库的键不许覆盖）。",
        )
    if edit.key_index_of(facts["bundle_name"]) is not None:
        raise AssetError(
            f"catalog 里已经有这条包登记：{facts['bundle_name']!r}",
            hint="同名包已登记过 ⇒ 先 newasset restore（同一个键派生同一个包名）。",
        )
    # ★ 落点（唯一规则 = new_asset.install_landing）：JSON 那条路原本按
    #   `RUNTIME_PATH + \PC\ + 包名` 拼 internalId（= 真件形状，包在 aa\PC）⇒ 这里显式给
    #   改写后的那一条，包落在 `<游戏>\BA_Mod\`、catalog 指同一处。
    internal_id = bundle_internal_id(
        cw.RUNTIME_PATH + cw.PC_SEGMENT + str(facts["bundle_name"]), facts["bundle_name"]
    )
    registration = edit.add_bundle(
        cw.CatalogBundle(
            bundle_name=facts["bundle_name"],
            m_hash=facts["m_hash"],
            crc=facts["crc"],
            bundle_size=facts["bundle_size"],
            internal_name=facts["internal_name"],
            internal_id=internal_id,
        )
    )
    if kind == "model":
        if not dependency_source_key:
            raise AssetError(
                "模型地址必须给依赖来源键（dependency_source_key）",
                hint="模型地址带依赖组；依赖 hash 只从一条**真件 prefab 地址**上抄，不许瞎编。",
            )
        dependency_hash = dependency_hash_from(edit, str(dependency_source_key))
        group = edit.add_dependency_group([registration.entry_index])
        address = edit.add_address(
            cw.CatalogAddress(asset_path=asset_path, primary_key=key, resource_type=want_type),
            dependency_key=group,
            dependency_hash=dependency_hash,
        )
        edit.assert_consistent()
        return edit.to_bytes(), Registration(
            key_index=address.key_index,
            bundle_key_index=registration.key_index,
            bundle_entry_index=registration.entry_index,
            asset_entry_index=address.entry_index,
            alias_entry_index=-1,
            internal_id_index=address.internal_id_index,
            counts_before=counts_before,
            counts_after=edit.counts,
            kind="model",
            dependency_group_index=group,
            dependency_hash=dependency_hash,
            dependency_source_key=str(dependency_source_key),
            address_row=tuple(int(v) for v in edit.entries[address.entry_index]),
            internal_id=internal_id,
        )
    address = edit.add_address(
        cw.CatalogAddress(asset_path=asset_path, primary_key=key, resource_type=want_type),
        dependency_key=registration.key_index,
    )
    dependency_hash = edit.entries[address.entry_index][3]
    alias = edit.add_entry(
        [
            address.internal_id_index,
            cw.BUNDLED_ASSET_PROVIDER,
            registration.key_index,
            dependency_hash,
            -1,
            address.key_index,
            4,
        ]
    )
    edit.buckets[address.key_index] = [address.entry_index, alias]
    edit.assert_consistent()
    return edit.to_bytes(), Registration(
        key_index=address.key_index,
        bundle_key_index=registration.key_index,
        bundle_entry_index=registration.entry_index,
        asset_entry_index=address.entry_index,
        alias_entry_index=alias,
        internal_id_index=address.internal_id_index,
        counts_before=counts_before,
        counts_after=edit.counts,
        address_row=tuple(int(v) for v in edit.entries[address.entry_index]),
        internal_id=internal_id,
    )


# --------------------------------------------------------------------------- #
# ⑤ 数据库那一行（我们自己的 BA_Mod.zip）
# --------------------------------------------------------------------------- #
def _zip_missing_hint(game_root: Path) -> str:
    name = external_db.game_zip_path(game_root).name
    return (
        "游戏根那份 BA_Mod.zip 是游戏**真正加载**的那份（DLL 补丁认固定文件名）。"
        f"先导出它：.venv\\Scripts\\python.exe -m ba_mod_maker.cli extract {game_root} "
        f"--out {game_root}\\{name}"
        "（⛔ 别去开 7.1 GB 的 data.unity3d，也别拿工程目录里那份当基线）"
    )


def _row_candidates(matches: list[tuple[int, dict[str, Any]]], *, limit: int) -> str:
    return "、".join(
        f"Id={row.get('Id')}（Name={row.get('Name')!r} 现键={row.get('ThumbnailFileName')!r}）"
        for _index, row in matches[:limit]
    )


def _near_candidates(rows: list[dict[str, Any]], unit: str) -> str:
    head = unit.split("\\", maxsplit=1)[0].split("/", maxsplit=1)[0]
    near = [row for row in rows if head and head in str(row.get("ModelFileName"))]
    if not near:
        near = rows[:3]
    return "、".join(f"{row.get('ModelFileName')!r}" for row in near[:5])


def find_unit_row(
    rows: list[dict[str, Any]], *, unit: str, row_id: int | None = None
) -> tuple[int, dict[str, Any]]:
    """在 Units 里按 ModelFileName 找**那一行**（重名 fail-closed）。

    ⚠ 实测游戏根那份 BA_Mod.zip：542 行里 ModelFileName **有 13 个重名**
    （例 RU_BMD-2\\RU_BMD_2_base 同时是 Id 90 与 478）⇒ 命中多于一条时**不猜**，
    报错并列出每条 Id / Name / 现 ThumbnailFileName，要求 --id。
    """
    matches = [
        (index, row) for index, row in enumerate(rows) if str(row.get("ModelFileName")) == unit
    ]
    if row_id is not None:
        exact = [(index, row) for index, row in matches if int(row.get("Id", -1)) == int(row_id)]
        if not exact:
            raise AssetError(
                f"Units 里 ModelFileName == {unit!r} 的行里没有 Id == {row_id}",
                hint="候选：" + _row_candidates(matches, limit=8),
            )
        matches = exact
    if not matches:
        raise AssetError(
            f"Units 里没有 ModelFileName == {unit!r} 的行",
            hint="--unit 要 **Units.ModelFileName 的原样值**（例 RU_BMD-2\\RU_BMD_2_base）。"
            "名字相近的候选：" + _near_candidates(rows, unit),
        )
    if len(matches) > 1:
        raise AssetError(
            f"ModelFileName == {unit!r} 在 Units 里有 {len(matches)} 行（这个列不唯一）",
            hint="加 --id 指定是哪一行："
            + _row_candidates(matches, limit=8)
            + "；想一次改多条请逐条跑（本单不做批量 —— 批量是复合动作）。",
        )
    return matches[0]


def _rows_of(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict) and isinstance(raw.get("Rows"), list):
        return raw["Rows"]
    if isinstance(raw, list):
        return raw
    raise AssetError(
        "Units.json 不是「一个数组」也不是「{Rows: [...]}」",
        hint="这份 BA_Mod.zip 的表结构我们不认识；别拿别的工具的产物当基线。",
    )


def _units_canonical(raw: Any) -> bytes:
    return json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True, slots=True)
class RowEdit:
    """Units 那一行改了什么 ＋ 新的整份 zip 字节。"""

    row_index: int
    row_id: int
    row_name: str
    old_key: str
    table_count: int
    zip_after: bytes
    #: 用户看得见的名字（`Units.HUDName`，缺了退回 `Name`）—— 报给用户一律用它（AGENTS §0.13）。
    row_hud: str = ""


def rewrite_zip_row(
    zip_path: str | Path,
    *,
    unit: str,
    key: str,
    row_id: int | None = None,
    column: str = "ThumbnailFileName",
    table: str = "Units",
    row_index: int | None = None,
) -> RowEdit:
    """把 Units 里那一行的 `column` 指到新键，返回**新的整份 zip 字节**。

    `column` 就是这条链的两种 kind：图标写 `ThumbnailFileName`（默认，改前改后一字不变），
    模型写 `ModelFileName` —— 两列**都是 catalog 地址键**（真件实测 Id 9：
    `ThumbnailFileName='RU_T72B-Label'` vs `ModelFileName='RU_T72B'`，⛔ 别写混）。

    两条 fail-closed（都有正控用例）：

    1. 命中的行**必须唯一**（ModelFileName 实测有 13 个重名 ⇒ 不唯一就要 --id）；
    2. Units.json 必须**已经是我们的序列化口径**（重写前后逐字节相同）——
       否则重写会让整张表变形，反向 Δ 退化成整份表（≈ 拷整包当备份，ADR 0007 不许）。
    """
    package = dbzip.DbZip.read(zip_path)
    table_before = package.tables.get(table)
    if table_before is None:
        raise AssetError(
            f"{Path(zip_path).name} 里没有 {table} 表",
            hint="外部库必须含 24 张表（游戏对缺的表是【不加载】而不是保留旧值）。",
        )
    raw = json.loads(table_before.decode("utf-8"))
    rows = _rows_of(raw)
    if row_index is not None:
        if not 0 <= int(row_index) < len(rows):
            raise AssetError(
                f"{table} 行下标 {row_index} 越界（共 {len(rows)} 行）",
                hint="用 `rows <源> <表>` 看行下标（0 起）。",
            )
        chosen = int(row_index)
        row = rows[chosen]
    else:
        chosen, row = find_unit_row(rows, unit=unit, row_id=row_id)
    old_key = str(row.get(column, ""))
    if _units_canonical(raw) != table_before:
        raise AssetError(
            f"{table} 的写法与我们的序列化口径不一致（重写会让整张表变形）",
            hint="那样反向 Δ 会退化成整份表（≈ 拷整包当备份，ADR 0007 不许）⇒ 拒绝。"
            "换一份由本产品导出的 BA_Mod.zip 再来。",
        )
    if old_key == key:
        raise AssetError(
            f"{table}[{chosen}].{column} 已经是 {key!r} 了",
            hint="没有要新增的东西；要重做先 newasset restore。",
        )
    row[column] = key
    package.tables[table] = _units_canonical(raw)
    return RowEdit(
        row_index=chosen,
        row_id=int(row.get("Id", -1)),
        row_name=str(row.get("Name", "")),
        old_key=old_key,
        table_count=len(package.tables),
        zip_after=_zip_bytes(package),
        row_hud=str(row.get("HUDName") or row.get("Name") or ""),
    )


# --------------------------------------------------------------------------- #
# 计划与报告
# --------------------------------------------------------------------------- #
def catalog_hash_bytes(catalog: bytes) -> bytes:
    """`aa/catalog.hash` 的**内容**：`hash128_hex(整份 catalog)` 的 32 个 ASCII 字符，**无换行**。

    口径出处（唯一）：`core/assets/catalog_bin.py` 的外部 oracle —— 真件 `aa/catalog.hash`
    == `hash128_hex(整份 aa/catalog.bin)`（`81d9efef07f4563f793992aaf1fa1717` 逐字相同）。
    ⛔ 哈希实现只有一份（`catalog_bin_write.hash128_hex`，SpookyHash V2 官方源码逐行移植）——
    别在别处再写一个；这里按需 import（对 1.2.0 那条 JSON 路是死代码）。
    """
    from ba_mod_maker.core.assets import catalog_bin_write as cbw

    return cbw.hash128_hex(catalog).encode("ascii")


def _describe_catalog_hash(before: bytes, after: bytes) -> str:
    """一行人话读数：`catalog.hash` 装前 -> 装后（装前盘上没有时印「（盘上没有）」）。"""
    was = before.decode("ascii", "replace") if before else "（盘上没有）"
    now = after.decode("ascii", "replace")
    return f"{was} -> {now}"


@dataclass(slots=True)
class AddPlan:
    """一次「新增」的完整计划。干跑就打到这一份上 —— 它**不含任何写操作**。"""

    game_root: Path
    project_root: Path
    png: Path
    fit: str
    key: str
    unit: str
    row_id: int
    row_index: int
    row_name: str
    old_key: str
    asset_path: str
    bundle_name: str
    bundle_data: bytes
    bundle_crc32: int
    bundle_serialized_size: int
    image_display: bytes
    image_stored: bytes
    source_size: tuple[int, int]
    meta_bundle: Path
    sprite_bundle: Path
    catalog_path: Path
    catalog_before: bytes
    catalog_after: bytes
    #: `aa/catalog.hash`（内容 = `hash128_hex(catalog)`）—— 它与 catalog **同步落盘**：
    #: 盘上「新 bin + 旧 hash」会让运行时按旧哈希比对（kb/40 §2.4.1 那条未决项）。
    catalog_hash_path: Path
    catalog_hash_before: bytes
    catalog_hash_after: bytes
    registration: Registration
    zip_path: Path
    zip_before: bytes
    zip_after: bytes
    table_count: int
    dll_state: str
    dll_sha256: str

    @property
    def kind(self) -> str:
        """这条计划是哪一种「新增」（见 KINDS）。AddPlan 就是**图标**那一档。"""
        return "label"

    @property
    def bundle_path(self) -> Path:
        return bundle_target_path(self.game_root, self.bundle_name)

    @property
    def bundle_sha256(self) -> str:
        return hashlib.sha256(self.bundle_data).hexdigest()

    def restore_command(self) -> str:
        return (
            ".venv\\Scripts\\python.exe -m ba_mod_maker.cli newasset restore "
            f"--game {self.game_root} --project-root {self.project_root}"
        )

    def plan_lines(self) -> tuple[str, ...]:
        """给人读的计划（干跑 / 真装共用的前半段）。"""
        stored_diff = first_difference(self.image_stored, self.image_display)
        round_trip = first_difference(
            flip_rows(self.image_stored, LABEL_WIDTH, LABEL_HEIGHT), self.image_display
        )
        return (
            "[资产新增] 计划",
            f"  游戏目录 : {self.game_root}",
            f"  工程目录 : {self.project_root}",
            f"  图       : {self.png}（{self.source_size[0]}x{self.source_size[1]} -> "
            f"{LABEL_WIDTH}x{LABEL_HEIGHT}，fit={self.fit}）",
            f"  键       : {self.key}  ->  Units[{self.row_index}] "
            f"Id={self.row_id} Name={self.row_name!r}",
            "① 新包",
            f"  {self.bundle_path}",
            f"  字节 {len(self.bundle_data):,} B / sha256 {self.bundle_sha256[:16]}… / "
            f"crc32(解压流 {self.bundle_serialized_size:,} B) = {self.bundle_crc32}",
            f"  类型树（只读照抄）: {self.meta_bundle.name}",
            f"  Sprite 形状模板（只读）: {self.sprite_bundle.name}",
            "② 像素",
            f"  416x216 RGBA = {len(self.image_display):,} B；存储行序 = 自下而上："
            f"与源图首个分歧下标 {stored_diff}（None 只可能出现在单行图上）；"
            f"翻回来 == 源图：{round_trip is None}",
            "③ catalog",
            f"  {self.catalog_path}",
            f"  {self.registration.summary()}",
            f"  包 internalId: {self.registration.internal_id}（与 ① 那个目录**同一处**）",
            f"  字节 {len(self.catalog_before):,} -> {len(self.catalog_after):,}"
            f"（{len(self.catalog_after) - len(self.catalog_before):+,}）",
            f"  catalog.hash: {self.catalog_hash_path}",
            f"  {_describe_catalog_hash(self.catalog_hash_before, self.catalog_hash_after)}",
            "④ 数据库",
            f"  {self.zip_path}",
            f"  Units[{self.row_index}].ThumbnailFileName: {self.old_key!r} -> {self.key!r}"
            f"（共 {self.table_count} 张表，只有 Units 那张被重写）",
            f"  字节 {len(self.zip_before):,} -> {len(self.zip_after):,}"
            f"（{len(self.zip_after) - len(self.zip_before):+,}）",
            "⑤ DLL",
            f"  {self.dll_state}；sha256 {self.dll_sha256[:16]}…",
            "撤",
            f"  {self.restore_command()}",
        )


def first_difference(left: bytes, right: bytes) -> int | None:
    """两个缓冲第一个不同的下标（None = 逐字节相同）。尺子的一行读数，别用眼睛比。"""
    limit = min(len(left), len(right))
    for index in range(limit):
        if left[index] != right[index]:
            return index
    return None if len(left) == len(right) else limit


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(slots=True)
class AddReport:
    """真装完（或干跑）的读数。"""

    plan: AddPlan | ModelAddPlan
    dry_run: bool
    patch_bytes: int = 0
    catalog_sha256: str = ""
    bundle_sha256: str = ""
    zip_sha256: str = ""
    install_verified: bool = False
    install_action: str = ""
    dll_line: str = ""
    dll_applied: bool = False
    manifest: Path | None = None
    verified: bool = False

    def lines(self) -> tuple[str, ...]:
        out = list(self.plan.plan_lines())
        out[0] = (
            "[资产新增] 干跑（游戏目录 / 工程目录一个字节都没写）"
            if self.dry_run
            else "[资产新增] 已装"
        )
        out.append("⑥ 落盘复核")
        if self.dry_run:
            out.append("  干跑：游戏目录 / 工程目录零写入")
            out.append("（待装的 zip 只在系统临时目录里拼过一次，随即删除）")
            out.append(f"  DLL  : {self.dll_line}")
            return tuple(out)
        out.append(f"  新包    : 写后读回 sha256 {self.bundle_sha256[:16]}…，crc32 与计划一致")
        out.append(f"  catalog : {self.catalog_sha256[:16]}…")
        if self.install_verified:
            zip_line = f"  zip     : {self.zip_sha256[:16]}… 写后复核=✓"
        elif self.install_action == "same":
            zip_line = "  zip     : 落点已经是这份内容，没写盘"
        else:
            zip_line = f"  zip     : {self.zip_sha256[:16]}… 写后复核=✗"
        out.append(zip_line)
        out.append(f"  反向 Δ  : {self.patch_bytes:,} B（含删包那条：那是新文件，还原=删）")
        out.append(f"  DLL     : {self.dll_line}")
        if self.manifest is not None:
            out.append(f"  本单清单: {self.manifest}")
        out.append(f"  撤销    : {self.plan.restore_command()}")
        return tuple(out)


@dataclass(slots=True)
class RestoreOutcome:
    """一次「全撤」的读数。"""

    ok: bool = True
    lines_: tuple[str, ...] = ()
    dll_line: str = ""
    manifest: Path | None = None

    def lines(self) -> tuple[str, ...]:
        # 头一句不写「资产新增」：同一条 restore 现在也撤「改指向」（repoint）那一档。
        head = "[newasset] 还原完成（逐位）" if self.ok else "[newasset] 还原未完成"
        return (head, *self.lines_)


# --------------------------------------------------------------------------- #
# 计划：读真件、算全部读数（一个字节都不写）
# --------------------------------------------------------------------------- #
def plan_add(
    *,
    png: str | Path,
    unit: str,
    key: str,
    fit: str = "blur",
    row_id: int | None = None,
    game_root: str | Path,
    project_root: str | Path,
    meta_bundle: str | Path | None = None,
    sprite_bundle: str | Path | None = None,
    block_type: int = builder.BLOCK_LZ4HC,
) -> AddPlan:
    """把整条链**算出来**（读游戏目录），**一个字节都不写**。"""
    from PIL import Image

    root = Path(game_root)
    project = Path(project_root)
    if not game_mod.is_game_root(root):
        raise AssetError(
            f"这里不像游戏根目录（缺 BrokenArrow_Data\\StreamingAssets\\aa\\catalog.json）：{root}",
            hint="--game 指游戏安装目录（含 BrokenArrow.exe 那一层）。",
        )
    validated = validate_key(key)
    catalog_path = game_mod.catalog_file(root)
    zip_path = external_db.game_zip_path(root)
    if not zip_path.is_file():
        raise AssetError(f"游戏根里没有 {zip_path.name}：{zip_path}", hint=_zip_missing_hint(root))

    png_path = Path(png)
    if not png_path.is_file():
        raise AssetError(f"找不到图片：{png_path}", hint="--png 要给一个存在的图（PNG / JPG）")
    with Image.open(png_path) as handle:
        source_size = (handle.width, handle.height)
    image_display = fit_image(png_path, fit=fit)
    image_stored = to_unity_image_data(image_display, LABEL_WIDTH, LABEL_HEIGHT)

    pc_dir = game_mod.bundle_dir(root)
    meta_source = Path(meta_bundle) if meta_bundle else _pick_bundle(pc_dir, META_BUNDLE_PREFIX)
    sprite_source = (
        Path(sprite_bundle) if sprite_bundle else _pick_bundle(pc_dir, SPRITE_BUNDLE_PREFIX)
    )
    plan_bundle = build_bundle(
        key=validated,
        image_display=image_display,
        meta_bundle=meta_source,
        sprite_bundle=sprite_source,
        block_type=block_type,
    )
    facts = bundle_facts(bundle_file_name(validated), plan_bundle.data, plan_bundle.crc32)

    catalog_before = catalog_path.read_bytes()
    catalog_after, registration = add_catalog_entries(
        catalog_before, key=validated, asset_path=asset_path_of(validated), facts=facts
    )
    # ★ `aa/catalog.hash` 与 catalog **必须同步**：内容 = `hash128_hex(整份 catalog)`
    #   （32 个 ASCII 字符、无换行）—— 盘上留一份「新 bin + 旧 hash」就是给运行时一个错哈希。
    catalog_hash_path = game_mod.catalog_hash_file(root)
    catalog_hash_before = catalog_hash_path.read_bytes() if catalog_hash_path.is_file() else b""
    catalog_hash_after = catalog_hash_bytes(catalog_after)

    zip_before = zip_path.read_bytes()
    edit = rewrite_zip_row(zip_path, unit=unit, key=validated, row_id=row_id)

    status = dllpatch.status(root)
    return AddPlan(
        game_root=root,
        project_root=project,
        png=png_path,
        fit=fit,
        key=validated,
        unit=unit,
        row_id=edit.row_id,
        row_index=edit.row_index,
        row_name=edit.row_name,
        old_key=edit.old_key,
        asset_path=asset_path_of(validated),
        bundle_name=facts["bundle_name"],
        bundle_data=plan_bundle.data,
        bundle_crc32=plan_bundle.crc32,
        bundle_serialized_size=plan_bundle.serialized_file_size,
        image_display=image_display,
        image_stored=image_stored,
        source_size=source_size,
        meta_bundle=meta_source,
        sprite_bundle=sprite_source,
        catalog_path=catalog_path,
        catalog_before=catalog_before,
        catalog_after=catalog_after,
        catalog_hash_path=catalog_hash_path,
        catalog_hash_before=catalog_hash_before,
        catalog_hash_after=catalog_hash_after,
        registration=registration,
        zip_path=zip_path,
        zip_before=zip_before,
        zip_after=edit.zip_after,
        table_count=edit.table_count,
        dll_state=status.state.label,
        dll_sha256=status.dll_sha256,
    )


@dataclass(slots=True)
class ModelAddPlan:
    """一次「新增单位模型」的完整计划。干跑就打到这一份上 —— 它**不含任何写操作**。

    与 :class:`AddPlan`（图标线）的差别只有形状：那条链要现造一张贴图包、要写 `ThumbnailFileName`、
    catalog 是 (3,4) 两条 entry；这条链**收一个已造好的 UnityFS 包**（本命令一个字节都不改它）、
    写 `ModelFileName`、catalog 是 `resource_type=1` 一条 entry ＋ 一个依赖组。
    ⛔ 造包不在这里（那是 asset_kernel 的事）—— 本模块只负责「搬进去 ＋ 登记 ＋ 指路 ＋ 全撤」。
    """

    game_root: Path
    project_root: Path
    #: 我们造好的那个包（源；① 把它拷到 aa\PC 下）。
    bundle_source: Path
    key: str
    unit: str
    row_id: int
    row_index: int
    row_name: str
    old_key: str
    asset_path: str
    bundle_name: str
    bundle_data: bytes
    bundle_crc32: int
    bundle_serialized_size: int
    catalog_path: Path
    catalog_before: bytes
    catalog_after: bytes
    #: `aa/catalog.hash`（内容 = `hash128_hex(catalog)`）—— 它与 catalog **同步落盘**：
    #: 盘上「新 bin + 旧 hash」会让运行时按旧哈希比对（kb/40 §2.4.1 那条未决项）。
    catalog_hash_path: Path
    catalog_hash_before: bytes
    catalog_hash_after: bytes
    registration: Registration
    #: 依赖 hash 是从哪个键的那条真件模型地址上抄的。
    dependency_source_key: str
    zip_path: Path
    zip_before: bytes
    zip_after: bytes
    table_count: int
    dll_state: str
    dll_sha256: str

    #: 整包 PPtr 普查的读数（asset_kernel.unit_bundle.audit_package_pptrs）—— 干跑也打出来。
    pptr_audit: tuple[str, ...] = ()

    #: 这一单改的是哪张表（`Units` / `Turrets`）。2026-10-25 加：炮塔那一档
    #: （`--turret-row`）原来在日志里被印成 `Units[7]` —— 标签错，实际改的是 Turrets 行。
    table: str = "Units"

    @property
    def kind(self) -> str:
        """这条计划是哪一种「新增」（见 KINDS）。ModelAddPlan 就是**模型**那一档。"""
        return "model"

    @property
    def bundle_path(self) -> Path:
        return bundle_target_path(self.game_root, self.bundle_name)

    @property
    def bundle_sha256(self) -> str:
        return hashlib.sha256(self.bundle_data).hexdigest()

    def restore_command(self) -> str:
        return (
            ".venv\\Scripts\\python.exe -m ba_mod_maker.cli newasset restore "
            f"--game {self.game_root} --project-root {self.project_root}"
        )

    def plan_lines(self) -> tuple[str, ...]:
        """给人读的计划（干跑 / 真装共用）。⛔ 模型这条链**没有**「图片 / 像素」那一段。"""
        return (
            "[资产新增] 计划（单位模型）",
            f"  游戏目录 : {self.game_root}",
            f"  工程目录 : {self.project_root}",
            f"  包(源)   : {self.bundle_source}",
            f"  键       : {self.key}  ->  {self.table}[{self.row_index}] "
            f"Id={self.row_id} Name={self.row_name!r}",
            "① 新包（已造好；本命令只搬不改）",
            f"  {self.bundle_path}",
            *tuple("  整包 PPtr 普查：" + line
                   for line in (self.pptr_audit or ("（未跑）",))),
            f"  字节 {len(self.bundle_data):,} B / sha256 {self.bundle_sha256[:16]}… / "
            f"crc32(解压流 {self.bundle_serialized_size:,} B) = {self.bundle_crc32}",
            "② catalog",
            f"  {self.catalog_path}",
            f"  {self.registration.summary()}",
            f"  包 internalId: {self.registration.internal_id}（与 ① 那个目录**同一处**）",
            f"  地址 entry#{self.registration.asset_entry_index} "
            f"{list(self.registration.address_row)}",
            f"  依赖 hash {self.registration.dependency_hash}"
            f"（抄自 {self.dependency_source_key!r} 那条真件模型地址的 entry[3]）",
            f"  字节 {len(self.catalog_before):,} -> {len(self.catalog_after):,}"
            f"（{len(self.catalog_after) - len(self.catalog_before):+,}）",
            f"  catalog.hash: {self.catalog_hash_path}",
            f"  {_describe_catalog_hash(self.catalog_hash_before, self.catalog_hash_after)}",
            "③ 数据库",
            f"  {self.zip_path}",
            f"  {self.table}[{self.row_index}].ModelFileName: {self.old_key!r} -> {self.key!r}"
            f"（共 {self.table_count} 张表，只有 {self.table} 那张被重写）",
            f"  字节 {len(self.zip_before):,} -> {len(self.zip_after):,}"
            f"（{len(self.zip_after) - len(self.zip_before):+,}）",
            "④ DLL",
            f"  {self.dll_state}；sha256 {self.dll_sha256[:16]}…",
            "撤",
            f"  {self.restore_command()}",
        )


# --------------------------------------------------------------------------- #
# 解析链核对：把「游戏 Addressables 会怎么走」逐跳验一遍（只读）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class AddressChainReport:
    """一条地址的**解析链**读数。`problems` 非空 = 游戏大概率取不到这个资产。"""

    key: str
    asset_path: str
    bundle_name: str
    bundle_file: str
    steps: tuple[str, ...]
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems

    def lines(self) -> tuple[str, ...]:
        return (*self.steps, *(f"★ {p}" for p in self.problems))


def verify_registered_address(
    *,
    game_root: str | Path,
    key: str,
    asset_path: str | None = None,
) -> AddressChainReport:
    """模拟游戏的 Addressables 解析：**键 → 地址条目 → 依赖组 → 包登记 → 文件 → 包内容器**。

    这条链上每一跳都是"游戏取不到就 Debug.Break / 报 'Addressable prefab … was not loaded'"的原因，
    而**离线**就能逐跳核（2026-10-08 加；起因：#711 第一版包里 MonoScript 缺失、
    以及"改了包却忘了更新 catalog 里那条登记的 m_Crc"这类错都出在这条链上）。

    跳数（每跳一行读数）：
      1. catalog 里有这个键，且它的**地址条目**形状对（provider / resource_type / 依赖组非 -1）；
      2. 依赖组那个桶里有且**只有一条**包登记（provider = AssetBundle、internalId 以 bundle 收尾）；
      3. 那个包文件在 `aa\\PC` 里**存在**，且 `m_Crc` / `m_BundleSize` 与磁盘**逐位相符**；
      4. 包里的**容器**里含这条 `asset_path`（逐字符）—— 这是 Addressables 取资产用的那个串。
    """
    game = Path(game_root)
    aa = game / "BrokenArrow_Data" / "StreamingAssets" / "aa"
    catalog_path = aa / "catalog.json"
    if not catalog_path.is_file() and (aa / "catalog.bin").is_file():
        # 1.2.1 起是二进制 catalog.bin；这条 JSON 解析链不吃它。
        # 直接调用方要读数就用 `ba-mod-maker catalog key <键>`（走 catalog_bin_write）。
        return AddressChainReport(
            key, asset_path or "", "", "",
            ("跳过：1.2.1 是二进制 catalog.bin，本链只吃 catalog.json",),
            ("地址链未核（不是资产错误）；用 catalog key 核",),
        )
    steps: list[str] = []
    problems: list[str] = []

    edit = ce.load_catalog_edit(catalog_path)
    ki = edit.key_index_of(key)
    if ki is None:
        return AddressChainReport(key, asset_path or "", "", "", (f"catalog {edit.counts_line()}",),
                                  (f"catalog 里没有键 {key!r}",))
    steps.append(f"① catalog 里有键 {key!r}（下标 {ki}）；{edit.counts_line()}")

    addr_entry = None
    for e in edit.buckets[ki]:
        row = edit.entries[e]
        if (row[ce.ENTRY_PRIMARY_KEY] == ki
                and row[ce.ENTRY_PROVIDER] == cw.ASSET_BUNDLE_PROVIDER + 2):
            addr_entry = e
            break
    if addr_entry is None:                       # provider=2 = 资产（不是包那条）
        for e in edit.buckets[ki]:
            row = edit.entries[e]
            if row[ce.ENTRY_PRIMARY_KEY] == ki and row[ce.ENTRY_DEPENDENCY_KEY] != -1:
                addr_entry = e
                break
    if addr_entry is None:
        problems.append(f"键 {key!r} 的桶里没有一条带依赖组的资产条目（地址形状不对）")
        return AddressChainReport(key, asset_path or "", "", "", tuple(steps), tuple(problems))
    row = edit.entries[addr_entry]
    internal = str(edit.internal_ids[row[ce.ENTRY_INTERNAL_ID]])
    dep_key = int(row[ce.ENTRY_DEPENDENCY_KEY])
    if asset_path and internal != asset_path:
        problems.append(f"地址条目里的 internalId {internal!r} 与给定的 asset_path 不一致")
    steps.append(f"② 地址条目 #{addr_entry}：provider={row[ce.ENTRY_PROVIDER]} "
                 f"resource_type={row[ce.ENTRY_RESOURCE_TYPE]} 依赖组={dep_key} "
                 f"→ {internal}")

    bundle_entry = None
    # ⚠ 依赖组那条**键**的桶里装的是**包条目**，但包条目的 primaryKey 是它自己的包名键
    #   （不是这个组键）—— 第一版多加了「primaryKey == dep_key」这个条件，于是真件也判成
    #   「没有包登记」（假红）。
    for e in (edit.buckets[dep_key] if 0 <= dep_key < len(edit.buckets) else ()):
        if edit.is_bundle_entry(e):
            bundle_entry = e
            break
    if bundle_entry is None:
        problems.append(f"依赖组 {dep_key} 的桶里没有包登记（provider=AssetBundle）")
        return AddressChainReport(key, internal, "", "", tuple(steps), tuple(problems))
    b = edit.entries[bundle_entry]
    bundle_internal = str(edit.internal_ids[b[ce.ENTRY_INTERNAL_ID]])
    bundle_name = bundle_internal.replace("\\", "/").rsplit("/", 1)[-1]
    options = cw.entry_options(edit.extra_data, int(b[ce.ENTRY_EXTRA_OFFSET])) or {}
    steps.append(f"③ 包登记 #{bundle_entry}：{bundle_name}（m_Crc={options.get('m_Crc')} "
                 f"m_BundleSize={options.get('m_BundleSize')} m_Hash={options.get('m_Hash')!r}）")

    path = aa / "PC" / bundle_name
    if not path.is_file():
        problems.append(f"包文件不在：{path}")
        return AddressChainReport(key, internal, bundle_name, str(path),
                                  tuple(steps), tuple(problems))
    # ⚠ bundle_facts_of_file 的第三个返回值是**解压流字节数**，不是 crc32
    #   （crc 在 facts["crc"] 里）—— 第一版按"三值 = facts/data/crc"读 ⇒ 真件也报不符（假红）。
    facts, _data, serialized_size = bundle_facts_of_file(path)
    crc = int(facts["crc"])
    steps.append(f"④ 磁盘：{path.stat().st_size:,} B · 解压流 {serialized_size:,} B"
                 f" · crc32(解压流) {crc}")
    if int(options.get("m_Crc", -1)) != crc:
        problems.append(f"m_Crc 与磁盘不符：catalog {options.get('m_Crc')} vs 磁盘 {crc}"
                        "（改了包没更新登记就是这个症状）")
    if int(options.get("m_BundleSize", -1)) != int(path.stat().st_size):
        problems.append(f"m_BundleSize 与磁盘不符：catalog {options.get('m_BundleSize')} "
                        f"vs 磁盘 {path.stat().st_size:,}")
    if options.get("m_Hash") and facts.get("m_hash") and options["m_Hash"] != facts["m_hash"]:
        problems.append(f"m_Hash 与文件名派生值不符：catalog {options['m_Hash']!r} "
                        f"vs {facts['m_hash']!r}")

    names: list[str] = []
    try:
        with ufs.UfsReader(path) as reader:
            node0 = reader.nodes[0]
            info = ufs.read_serialized_file(reader, node0)
            meta = mw.parse_serialized_metadata(reader.read_range(node0.offset, info.data_offset))
            cid = {i: t.class_id for i, t in enumerate(meta.types)}
            common = clone.unitypy_common_strings()
            for o in info.objects:
                if cid.get(o[4]) != builder.CLASS_ASSETBUNDLE:
                    continue
                f = clone.read_object(
                    bytes(reader.read_range(node0.offset + info.data_offset + o[2], o[3])),
                    clone.read_type_nodes(
                        struct.pack("<ii", meta.types[o[4]].type_tree.node_count,
                                    meta.types[o[4]].type_tree.string_buffer_size)
                        + meta.types[o[4]].type_tree.nodes + meta.types[o[4]].type_tree.strings,
                        common_strings=common))
                names += [str((p or {}).get("first") or "") for p in (f.get("m_Container") or [])]
    except Exception as exc:  # noqa: BLE001
        problems.append(f"包读不开：{type(exc).__name__}: {exc}")
    steps.append(f"⑤ 包内容器 {len(names)} 条：{names}")
    if asset_path and names and resolve_container_path(names, str(asset_path)) is None:
        problems.append(f"包内容器里没有 {asset_path!r}（Addressables 按这个串取资产）")
    if not names:
        problems.append("包内容器是空的")
    return AddressChainReport(key, internal, bundle_name, str(path), tuple(steps), tuple(problems))


def _container_asset_path(bundle: str | Path) -> str | None:
    """包里**唯一**那条 `.prefab` 容器地址；不是 1 条就 None（调用方退回按 key 派生）。

    2026-10-29 现场：手填 `Assets/_unitbuild/<时间戳>/…`（Unity 的**临时工作 prefab**，包里没有这个
    地址）⇒ 游戏报 `Addressable prefab '<key>' was not loaded!`，军械库无卡片无模型。
    游戏真按的是**容器地址**，所以默认值只能从包里读。
    """
    try:
        from ba_mod_maker.asset_kernel import unitypy_source

        addrs = [a for a in unitypy_source.containers_map_via_ufs(Path(bundle))
                 if a.lower().endswith(".prefab")]
    except Exception:  # noqa: BLE001 —— 读不开包就退回按 key 派生，别让默认值挡住主流程
        return None
    return addrs[0] if len(addrs) == 1 else None


def plan_add_model(
    *,
    bundle: str | Path,
    unit: str,
    key: str,
    game_root: str | Path,
    project_root: str | Path,
    asset_path: str | None = None,
    row_id: int | None = None,
    dependency_source: str | None = None,
    dependency_policy: str = "own-only",
    table: str = "Units",
    row_index: int | None = None,
    facts: Mapping[str, Any] | None = None,
) -> ModelAddPlan:
    """把「新增一个单位模型」整条链**算出来**（读游戏目录 ＋ 读那个包），**一个字节都不写**。

    参数
    ----
    bundle
        我们造好的 UnityFS 包（**不改它**，只搬）：catalog 的四个值从它身上现读。
    unit / row_id
        要指过去的 Units 那一行（`unit` 是 `ModelFileName` 的**原样值**，重名时用 `row_id`）。
    key / asset_path
        新的 catalog 地址键 / 资产路径（不给就按 `model_asset_path_of` 派生）。
    dependency_source
        依赖 hash 抄谁。默认 = 这一行**现在的** `ModelFileName`（我们正要取代的那条真件地址）——
        口径与 routeb_install.py 相同，只是它当时把 RU_T72B 写死在脚本里。
    facts
        可选：装配那一步写下的四件套（例 facts.json）。给了就与包**对账**，对不上 fail-closed。
    """
    root = Path(game_root)
    project = Path(project_root)
    if not game_mod.is_game_root(root):
        raise AssetError(
            f"这里不像游戏根目录（缺 BrokenArrow_Data\\StreamingAssets\\aa\\catalog.json）：{root}",
            hint="--game 指游戏安装目录（含 BrokenArrow.exe 那一层）。",
        )
    validated = validate_key(key)
    catalog_path = game_mod.catalog_file(root)
    zip_path = external_db.game_zip_path(root)
    if not zip_path.is_file():
        raise AssetError(f"游戏根里没有 {zip_path.name}：{zip_path}", hint=_zip_missing_hint(root))

    source = Path(bundle)
    actual, data, serialized_size = bundle_facts_of_file(source)
    if facts is not None:
        verify_facts(facts, actual)
    # ★ 2026-10-08：**整包 PPtr 普查**（含 payload 对象 = 以前尺子的盲区）。
    #   引擎对悬空 PPtr 是**静默**的（实测），所以在这一步 fail-closed：
    #   带悬空的包一旦登记进 catalog，症状会出现在真机上（空指针 / 直接崩），很难往回想到这里。
    from ba_mod_maker.asset_kernel import unit_bundle as _unit_bundle

    try:
        audit = _unit_bundle.audit_package_pptrs(source)
    except Exception as exc:  # noqa: BLE001
        # 包**读不开**不是这条链的硬条件 —— 本模块只搬字节（图标线也不要求能解包）。
        # 实测：用例里的手搓空容器就走这一支。如实记读数，别静默。
        audit_lines = (f"整包 PPtr 普查：**跳过**（{type(exc).__name__}: {exc}）",)
    else:
        audit_lines = audit.lines()
        if not audit.ok:
            raise AssetError(
                f"这个包里有 {len(audit.dangling)} 条悬空 PPtr（指向包内不存在的 pathID）",
                hint="先跑 unit_bundle.audit_package_pptrs(包) 看明细："
                     + "；".join(audit.dangling[:3]),
            )

    # 先读数据库那一行：模型线的依赖来源默认就是它**现在的** ModelFileName。
    zip_before = zip_path.read_bytes()
    edit = rewrite_zip_row(
        zip_path,
        unit=unit,
        key=validated,
        row_id=row_id,
        column="ModelFileName",
        table=table,
        row_index=row_index,
    )
    source_key = str(dependency_source) if dependency_source else edit.old_key
    if not source_key and dependency_policy == "auto":
        raise AssetError(
            f"Units[{edit.row_index}] 的 ModelFileName 是空串 —— 没有可抄的依赖来源",
            hint="用 --dependency-source 显式给一条真件 prefab 地址的键（例 RU_T72B）。",
        )

    # ★ 2026-10-29：不给 `asset_path` 时**从包容器里读**（手填过一次 `Assets/_unitbuild/<ts>/…`，
    #   游戏报 `Addressable prefab '<key>' was not loaded!`）。读不出来才退回按 key 派生；
    #   两处（catalog 条目 / 计划对象）共用同一个值，别各算各的。
    resolved_asset_path = (str(asset_path) if asset_path
                           else _container_asset_path(source) or model_asset_path_of(validated))
    catalog_before = catalog_path.read_bytes()
    catalog_after, registration = add_catalog_entries(
        catalog_before,
        key=validated,
        asset_path=resolved_asset_path,
        facts=dict(actual),
        kind="model",
        dependency_source_key=source_key,
        dependency_policy=dependency_policy,
    )
    # ★ `aa/catalog.hash` 与 catalog **必须同步**：内容 = `hash128_hex(整份 catalog)`
    #   （32 个 ASCII 字符、无换行）—— 盘上留一份「新 bin + 旧 hash」就是给运行时一个错哈希。
    catalog_hash_path = game_mod.catalog_hash_file(root)
    catalog_hash_before = catalog_hash_path.read_bytes() if catalog_hash_path.is_file() else b""
    catalog_hash_after = catalog_hash_bytes(catalog_after)
    status = dllpatch.status(root)
    return ModelAddPlan(
        game_root=root,
        project_root=project,
        bundle_source=source,
        key=validated,
        unit=unit,
        row_id=edit.row_id,
        row_index=edit.row_index,
        row_name=edit.row_name,
        old_key=edit.old_key,
        asset_path=resolved_asset_path,
        bundle_name=actual["bundle_name"],
        bundle_data=data,
        bundle_crc32=int(actual["crc"]),
        bundle_serialized_size=serialized_size,
        catalog_path=catalog_path,
        catalog_before=catalog_before,
        catalog_after=catalog_after,
        catalog_hash_path=catalog_hash_path,
        catalog_hash_before=catalog_hash_before,
        catalog_hash_after=catalog_hash_after,
        registration=registration,
        dependency_source_key=source_key,
        zip_path=zip_path,
        zip_before=zip_before,
        zip_after=edit.zip_after,
        table_count=edit.table_count,
        dll_state=status.state.label,
        dll_sha256=status.dll_sha256,
        pptr_audit=audit_lines,
        table=table,
    )

@contextmanager
def _work_dir():
    """系统临时目录（待装的 zip 在这里拼一次，出这段就没了）。"""
    with tempfile.TemporaryDirectory(prefix="ba-mod-maker-asset-add-") as folder:
        yield Path(folder)


def _zip_bytes(package: dbzip.DbZip) -> bytes:
    """把一份外部库对象变成字节（DbZip.write 要路径 ⇒ 借临时目录走一趟）。

    为什么不自己拼 zip：写出顺序、24 张表的别名条目（单数 PlaneFlyPreset.json）
    都是 dbzip.DbZip.write 的口径，抄第二份必然漂移（AGENTS §5）。
    """
    with _work_dir() as folder:
        return package.write(folder / WORK_ZIP_NAME).read_bytes()


# --------------------------------------------------------------------------- #
# 真装
# --------------------------------------------------------------------------- #
def _write_verified(path: Path, data: bytes, *, what: str) -> None:
    """写一个文件并**读回来核对**（写盘出事不许留下半成品）。

    父目录顺手建出来：新包的落点是 `<游戏根>\\BA_Mod\\`（`install_landing`），
    第一次装时机上**还没有**这个目录（`aa\\PC` 是游戏自己的、一直在）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".newasset.tmp")
    tmp.unlink(missing_ok=True)
    try:
        tmp.write_bytes(data)
        if tmp.read_bytes() != data:
            raise AssetError(
                f"{what}写后复核不通过：临时文件与预期不一致（{tmp}）",
                hint="落点没被替换，原来那份还在。重试一次；反复出现就把日志报上来。",
            )
        os.replace(tmp, path)
        if path.read_bytes() != data:
            raise AssetError(f"{what}写后复核不通过：{path} 的内容与预期不符")
    finally:
        tmp.unlink(missing_ok=True)


def manifest_path(project_root: str | Path) -> Path:
    """本单清单的路径（跟补丁柜同一个目录）。"""
    return rd.patch_store_root(project_root) / MANIFEST_NAME


def _write_manifest(plan: AddPlan | ModelAddPlan, *, dll_applied: bool) -> Path:
    """写本单清单。

    ⚠ **只升不降**：同一份补丁柜里连着装两次时（第二次 DLL 本来就打着 ⇒ dll_applied=False），
    不能把第一次记下的 True 抹掉 —— 抹掉就再也没人认得出那个补丁是我们打的了。
    """
    path = manifest_path(plan.project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = _read_manifest(plan.project_root) or {}
    payload = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kind": getattr(plan, "kind", "label"),
        "key": plan.key,
        "unit": plan.unit,
        "row_id": plan.row_id,
        "row_index": plan.row_index,
        "asset_path": plan.asset_path,
        "bundle": str(plan.bundle_path),
        "bundle_sha256": plan.bundle_sha256,
        "catalog": str(plan.catalog_path),
        "zip": str(plan.zip_path),
        "dll_applied": bool(dll_applied or previous.get("dll_applied")),
        "dll_state_before": plan.dll_state,
        "dll_sha256_before": plan.dll_sha256,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _read_manifest(project_root: str | Path) -> dict[str, Any] | None:
    path = manifest_path(project_root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def run_add(
    plan: AddPlan | ModelAddPlan, *, force_dll: bool = False, check_game_running: bool = True
) -> AddReport:
    """按计划落盘：补丁 → catalog → 新包 → BA_Mod.zip → DLL。

    顺序的理由（与 external_db 同口径）：**先落补丁、再写盘** —— 崩在哪一步，
    原字节都还在补丁里，newasset restore 都能回到装前。
    """
    root, project = plan.game_root, plan.project_root
    target = plan.bundle_path
    if check_game_running and game_mod.is_game_running():
        raise AssetError(
            "游戏正在运行，无法新增资产",
            hint="游戏占着游戏目录里的文件时写入会失败（WinError 5）。请先退出游戏。",
        )
    if target.exists():
        raise AssetError(
            f"落点已经存在：{target}",
            hint="这个键派生出来的包名撞上了现场的包 ⇒ 先 newasset restore，或换一个 --key。",
        )
    status = dllpatch.status(root)
    if status.state is dllpatch.PatchState.MISSING:
        raise AssetError(
            f"找不到 {status.dll}",
            hint="--game 要指游戏安装目录（含 GameAssembly.dll 那一层）。",
        )
    if status.state is dllpatch.PatchState.NO_BACKUP and not force_dll:
        raise AssetError(
            f"{dllpatch.DLL_NAME} 被改过、而且游戏根目录里没有任何备份",
            hint="这种现场打不了补丁（也回不去）。用 Steam 校验完整性把 DLL 换回原版；"
            "⛔ 别手工改字节，等本工具补上这一版的地址表。",
        )
    if status.state is dllpatch.PatchState.MODIFIED and not force_dll:
        raise AssetError(
            f"当前 {dllpatch.DLL_NAME} 不是本工具已知的原版（地址表可能已失效）",
            hint="用 Steam 校验完整性把 DLL 换回原版，或等本工具补上这一版的地址表；"
            "⛔ 别手工改字节。",
        )

    report = AddReport(plan=plan, dry_run=False)
    label = f"资产新增 {plan.key}（{plan.unit}）"
    store = rd.PatchStore(rd.patch_store_root(project), game_root=root)
    recorder = store.begin(label)
    recorder.capture_file(plan.catalog_path, before=plan.catalog_before, after=plan.catalog_after)
    # catalog.hash 与 catalog 是**一对**：同一条补丁柜里一起记、一起撤（别拆成两笔）。
    # ⚠ `before` 传 `or None`：盘上原本没有这个文件时，补丁柜的口径是 `None` = 还原就删掉它；
    #   传 b"" 会被当成「装前是个空文件」⇒ 撤完在盘上留一个 0 字节的 catalog.hash。
    recorder.capture_file(
        plan.catalog_hash_path,
        before=plan.catalog_hash_before or None,
        after=plan.catalog_hash_after,
    )
    recorder.capture_file(target, before=None, after=plan.bundle_data)
    recorder.commit()
    report.manifest = _write_manifest(plan, dll_applied=False)

    try:
        _write_verified(plan.catalog_path, plan.catalog_after, what="catalog")
        report.catalog_sha256 = sha256_of(plan.catalog_after)
        _write_verified(plan.catalog_hash_path, plan.catalog_hash_after, what="catalog.hash")

        _write_verified(target, plan.bundle_data, what="新包")
        with ufs.UfsReader(target) as reader:
            stream = bytes(reader.read_range(0, reader.total_uncompressed))
            file_size = reader.file_size
        got_crc = zlib.crc32(stream) & 0xFFFFFFFF
        if got_crc != plan.bundle_crc32 or file_size != len(plan.bundle_data):
            raise AssetError(
                f"新包写后复核不通过：crc32 {got_crc}（期望 {plan.bundle_crc32}）/ "
                f"文件长 {file_size:,}（期望 {len(plan.bundle_data):,}）"
            )
        report.bundle_sha256 = sha256_of(plan.bundle_data)

        with _work_dir() as folder:
            work_zip = folder / WORK_ZIP_NAME
            work_zip.write_bytes(plan.zip_after)
            install = external_db.install(
                root,
                work_zip,
                project_root=project,
                label=label,
                check_game_running=check_game_running,
            )
        report.install_action = install.action
        report.install_verified = install.verified
        report.zip_sha256 = install.sha_after

        if status.state is dllpatch.PatchState.PATCHED:
            report.dll_applied = False
            report.dll_line = "本来就打着（**不是本单打的**）—— 本单不负责撤它，restore 只会印状态"
        else:
            report.dll_line = dllpatch.apply(
                root, force=force_dll, check_game_running=check_game_running
            )
            report.dll_applied = True
    except BaseException as exc:  # 出事必须用补丁撤回，且**不许吞**（所以捕 BaseException）
        done, undone = _undo(store)
        if done:
            manifest_path(project).unlink(missing_ok=True)
        hint = exc.hint if isinstance(exc, AssetError) else None
        raise AssetError(
            f"装 {plan.key} 失败：{exc}（{undone}）",
            hint=hint or "看上一句撤销结果；补丁还在的话可以再跑一次 newasset restore。",
            report=report,
        ) from exc

    if store.json_path.is_file() and store.blob_path.is_file():
        report.patch_bytes = store.json_path.stat().st_size + store.blob_path.stat().st_size
    report.manifest = _write_manifest(plan, dll_applied=report.dll_applied)
    report.verified = bool(report.install_verified or report.install_action == "same")
    return report


def _undo(store: rd.PatchStore) -> tuple[bool, str]:
    """写盘出事时用补丁撤回 —— 返回（现场是否已回到装前，一句人话）。"""
    if not store.has_pending():
        # external_db.install 自己撤过一次（它撤的就是同一条补丁）⇒ 现场已经回去了。
        return True, "补丁已不在（上一步自己撤过），现场已回到装前"
    ok, message = store.rollback()
    return ok, message


def run_add_dry(plan: AddPlan | ModelAddPlan) -> AddReport:
    """干跑的读数（不再读盘：计划里就有全部数字）。"""
    return AddReport(plan=plan, dry_run=True, dll_line=f"{plan.dll_state}（干跑没动它）")


# --------------------------------------------------------------------------- #
# 全撤
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 「只落包 + 只登记地址」那一档：⛔ 不写 BA_Mod.zip · ⛔ 不碰 Units 行 · ⛔ 不打 DLL
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class BundleAddPlan:
    """一次「只落包 + 只登记一条地址」的完整计划 —— 只有模块头 ①+② 那两段。

    与 :class:`ModelAddPlan` 的差别是**少了整两段**（不是少几个参数）：
    ⛔ 没有单位那一行（不读也不写 BA_Mod.zip）· ⛔ 没有 DLL 那一段。
    为什么要单独一份：那两段原本与「落包 + 登记」焊在 `plan_add_model` / `run_add`
    同一条链上（一段读 Units 行、一段打 DLL），而「把一个包登记进 catalog」本身与它们无关 ——
    焊在一起的代价是每一步不能再各自撤销（AGENTS §0.10：新增 / 删除要能各自回退）。
    """

    game_root: Path
    project_root: Path
    #: 我们造好的那个包（源；① 把它搬进 aa/PC，本命令一个字节都不改它）。
    bundle_source: Path
    key: str
    asset_path: str
    bundle_name: str
    bundle_data: bytes
    bundle_crc32: int
    bundle_serialized_size: int
    catalog_path: Path
    catalog_before: bytes
    catalog_after: bytes
    #: aa/catalog.hash（内容 = hash128_hex(catalog)）—— 与 catalog **同步落盘**。
    catalog_hash_path: Path
    catalog_hash_before: bytes
    catalog_hash_after: bytes
    registration: Registration
    #: 依赖声明抄自哪一条**已入库**的地址键（这一档必填，不许瞎编）。
    dependency_source_key: str
    #: BA_Mod.zip 的路径与**装前** sha256 —— 本命令只读它，装后复核逐位不变。
    zip_path: Path
    zip_sha256_before: str = ""
    #: 整包 PPtr 普查的读数（asset_kernel.unit_bundle.audit_package_pptrs）—— 干跑也打出来。
    pptr_audit: tuple[str, ...] = ()

    @property
    def kind(self) -> str:
        """见 KINDS；这一档只落包与地址，**不指任何一行**。"""
        return "bundle"

    @property
    def bundle_path(self) -> Path:
        return bundle_target_path(self.game_root, self.bundle_name)

    @property
    def bundle_sha256(self) -> str:
        return hashlib.sha256(self.bundle_data).hexdigest()

    def restore_command(self) -> str:
        return (
            ".venv\\Scripts\\python.exe -m ba_mod_maker.cli newasset restore "
            f"--game {self.game_root} --project-root {self.project_root}"
        )

    def plan_lines(self) -> tuple[str, ...]:
        """给人读的计划（干跑 / 真装共用）。⛔ 这份文本里没有单位那一行，也没有 DLL。"""
        return (
            "[资产新增] 计划（只落包 + 只登记地址）",
            f"  游戏目录 : {self.game_root}",
            f"  工程目录 : {self.project_root}",
            f"  包(源)   : {self.bundle_source}",
            f"  键       : {self.key}",
            f"  资产路径 : {self.asset_path}",
            "① 新包（已造好；本命令只搬不改）",
            f"  {self.bundle_path}",
            *tuple("  整包 PPtr 普查：" + line for line in (self.pptr_audit or ("（未跑）",))),
            f"  字节 {len(self.bundle_data):,} B / sha256 {self.bundle_sha256[:16]}… / "
            f"crc32(解压流 {self.bundle_serialized_size:,} B) = {self.bundle_crc32}",
            "② catalog（包登记 + 一条地址 + 同步 catalog.hash）",
            f"  {self.catalog_path}",
            f"  {self.registration.summary()}",
            f"  包 internalId: {self.registration.internal_id}（与 ① 那个目录**同一处**）",
            f"  依赖 hash {self.registration.dependency_hash}"
            f"（抄自 {self.dependency_source_key!r} 那条地址的依赖声明）",
            f"  字节 {len(self.catalog_before):,} -> {len(self.catalog_after):,}"
            f"（{len(self.catalog_after) - len(self.catalog_before):+,}）",
            f"  catalog.hash: {self.catalog_hash_path}",
            f"  {_describe_catalog_hash(self.catalog_hash_before, self.catalog_hash_after)}",
            "③ 数据库（单位那一行）：**本命令不动**",
            f"  {self.zip_path}"
            f"（只读核对 sha256 {self.zip_sha256_before[:16] or '（盘上没有）'}…，装后须逐位不变）",
            "④ DLL（补丁那一段）：**本命令不动**",
            "撤",
            f"  {self.restore_command()}",
        )


@dataclass(slots=True)
class BundleAddReport:
    """真装完（或干跑）的读数。

    与 :class:`AddReport` 分开一份的理由：那一份的尾巴印的是 zip 写回复核与 DLL 那一行，
    而这一档两件都没有 —— 沿用会印出**没发生的事**。
    """

    plan: BundleAddPlan
    dry_run: bool
    patch_bytes: int = 0
    catalog_sha256: str = ""
    bundle_sha256: str = ""
    zip_sha256: str = ""
    manifest: Path | None = None
    verified: bool = False

    def lines(self) -> tuple[str, ...]:
        out = list(self.plan.plan_lines())
        out[0] = (
            "[资产新增] 干跑（游戏目录 / 工程目录一个字节都没写）"
            if self.dry_run
            else "[资产新增] 已装（只落包 + 只登记地址）"
        )
        out.append("⑤ 落盘复核")
        if self.dry_run:
            out.append("  干跑：游戏目录 / 工程目录零写入（连补丁柜都不建）")
            return tuple(out)
        out.append(f"  新包    : 写后读回 sha256 {self.bundle_sha256[:16]}…，crc32 与计划一致")
        out.append(f"  catalog : {self.catalog_sha256[:16]}…")
        out.append(
            f"  BA_Mod.zip: sha256 {self.zip_sha256[:16] or '（盘上没有）'}… —— "
            "与装前**逐位相同**（本命令没写它）"
        )
        out.append(f"  反向 Δ  : {self.patch_bytes:,} B（含删包那条：那是新文件，还原=删）")
        if self.manifest is not None:
            out.append(f"  本单清单: {self.manifest}")
        out.append(f"  撤销    : {self.plan.restore_command()}")
        return tuple(out)


def plan_add_bundle(
    *,
    bundle: str | Path,
    key: str,
    dependency_source: str,
    game_root: str | Path,
    project_root: str | Path,
    asset_path: str | None = None,
    facts: Mapping[str, Any] | None = None,
) -> BundleAddPlan:
    """把「只落一个包 + 只登记一条地址」算出来（读游戏目录 ＋ 读那个包），**一个字节都不写**。

    与 :func:`plan_add_model` 的唯一形状差别 = **不收 unit / row_id**（那两件属于数据库那一行），
    所以它**打不开也写不了** BA_Mod.zip —— 想顺手改一行是不可能的，这条链上没有那个对象。

    参数
    ----
    bundle
        我们造好的 UnityFS 包（**不改它**，只搬）：catalog 的四个值从它身上现读。
    key / asset_path
        新的 catalog 地址键 / 资产路径（不给就按 `model_asset_path_of` 派生）。
    dependency_source
        **必填**：依赖声明抄哪一条**已入库**的地址键（例 RU_T72B）。这一档没有「默认值」
        —— 模型那一档能默认成「这一行现在的 ModelFileName」，这一档连行都没有。
    facts
        可选：装配那一步写下的四件套（例 facts.json）。给了就与包**对账**，对不上 fail-closed。
    """
    root = Path(game_root)
    project = Path(project_root)
    if not game_mod.is_game_root(root):
        raise AssetError(
            f"这里不像游戏根目录（缺 BrokenArrow_Data\\StreamingAssets\\aa\\catalog.bin）：{root}",
            hint="--game 指游戏安装目录（含 BrokenArrow.exe 那一层）。",
        )
    validated = validate_key(key)
    source_key = str(dependency_source or "").strip()
    if not source_key:
        raise AssetError(
            "--dependency-source 是空的",
            hint="依赖声明只从一条**已入库**的地址上抄（例 RU_T72B）；这一档没有默认值。",
        )
    catalog_path = game_mod.catalog_file(root)
    source = Path(bundle)
    actual, data, serialized_size = bundle_facts_of_file(source)
    if facts is not None:
        verify_facts(facts, actual)
    # ★ 整包 PPtr 普查（与模型那一档同一把尺子）：引擎对悬空 PPtr 是**静默**的，
    #   所以在这一步 fail-closed —— 带悬空的包登记进 catalog，症状只会出现在真机上。
    from ba_mod_maker.asset_kernel import unit_bundle as _unit_bundle

    try:
        audit = _unit_bundle.audit_package_pptrs(source)
    except Exception as exc:  # noqa: BLE001
        # 包**读不开**不是这条链的硬条件 —— 本模块只搬字节。
        audit_lines = (f"整包 PPtr 普查：**跳过**（{type(exc).__name__}: {exc}）",)
    else:
        audit_lines = audit.lines()
        if not audit.ok:
            raise AssetError(
                f"这个包里有 {len(audit.dangling)} 条悬空 PPtr（指向包内不存在的 pathID）",
                hint="先跑 unit_bundle.audit_package_pptrs(包) 看明细："
                + "；".join(audit.dangling[:3]),
            )

    catalog_before = catalog_path.read_bytes()
    catalog_after, registration = add_catalog_entries(
        catalog_before,
        key=validated,
        asset_path=str(asset_path) if asset_path else model_asset_path_of(validated),
        facts=dict(actual),
        kind="model",
        dependency_source_key=source_key,
        dependency_policy="auto",
    )
    # ★ aa/catalog.hash 与 catalog **必须同步**：内容 = hash128_hex(整份 catalog)
    catalog_hash_path = game_mod.catalog_hash_file(root)
    catalog_hash_before = catalog_hash_path.read_bytes() if catalog_hash_path.is_file() else b""
    catalog_hash_after = catalog_hash_bytes(catalog_after)
    # BA_Mod.zip **只读**：记下装前 sha256，真装完再核一遍逐位不变（③ 的机械证据）。
    zip_path = external_db.game_zip_path(root)
    zip_sha = sha256_of(zip_path.read_bytes()) if zip_path.is_file() else ""
    return BundleAddPlan(
        game_root=root,
        project_root=project,
        bundle_source=source,
        key=validated,
        asset_path=str(asset_path) if asset_path else model_asset_path_of(validated),
        bundle_name=actual["bundle_name"],
        bundle_data=data,
        bundle_crc32=int(actual["crc"]),
        bundle_serialized_size=serialized_size,
        catalog_path=catalog_path,
        catalog_before=catalog_before,
        catalog_after=catalog_after,
        catalog_hash_path=catalog_hash_path,
        catalog_hash_before=catalog_hash_before,
        catalog_hash_after=catalog_hash_after,
        registration=registration,
        dependency_source_key=source_key,
        zip_path=zip_path,
        zip_sha256_before=zip_sha,
        pptr_audit=audit_lines,
    )


def _write_bundle_manifest(plan: BundleAddPlan) -> Path:
    """写本单清单（与 add / add-model **同一个文件**，多一条 `kind="bundle"`）。

    `dll_applied` **只升不降**（照 :func:`_write_manifest` 的口径）：这一档从不打 DLL，
    所以它自己永远写 False；但同一份补丁柜里若已经有别的入口记过 True，**不许抹掉** ——
    抹掉就再也没人认得出那个补丁是我们打的了。
    """
    path = manifest_path(plan.project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = _read_manifest(plan.project_root) or {}
    payload = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kind": "bundle",
        "key": plan.key,
        "asset_path": plan.asset_path,
        "bundle": str(plan.bundle_path),
        "bundle_sha256": plan.bundle_sha256,
        "catalog": str(plan.catalog_path),
        "zip": str(plan.zip_path),
        "zip_sha256_before": plan.zip_sha256_before,
        "dll_applied": bool(previous.get("dll_applied")),
        "dll_state_before": previous.get("dll_state_before", ""),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _assert_zip_untouched(plan: BundleAddPlan) -> str:
    """读一次 BA_Mod.zip 的 sha256，与计划里记的对账（⛔ 本命令不写它）。

    这是「不碰数据库」的**机械证据**：不是"我没写"，而是"两份读数逐位相同"。
    盘上本来没有它时返回空串（这一档压根不用数据库）。
    """
    if not plan.zip_sha256_before:
        return ""
    now = sha256_of(plan.zip_path.read_bytes())
    if now != plan.zip_sha256_before:
        raise AssetError(
            f"BA_Mod.zip 的内容变了（sha256 {plan.zip_sha256_before[:16]}… -> {now[:16]}…）",
            hint="这一档命令**不该**碰数据库；真变了说明同一条链上有别的东西在写它。",
        )
    return now


def run_add_bundle(plan: BundleAddPlan, *, check_game_running: bool = True) -> BundleAddReport:
    """按计划落盘：补丁 -> catalog -> catalog.hash -> 新包（**就这四件**）。

    ⛔ 与 :func:`run_add` 的三处不同，都是**故意**的：
    1. 不调 `external_db.install`、不碰 BA_Mod.zip（连临时目录都不拼一份）；
    2. 不调 `dllpatch.*`（连 status 都不看）；
    3. 收尾复核里多一条：BA_Mod.zip 的 sha256 与计划里记的**逐位相同** ——
       这是「本命令没碰数据库」的机械证据，不是靠嘴说。

    顺序的理由与 run_add 相同：**先落补丁、再写盘** —— 崩在哪一步，
    原字节都还在补丁里，newasset restore 都能回到装前。
    """
    root, project = plan.game_root, plan.project_root
    target = plan.bundle_path
    if check_game_running and game_mod.is_game_running():
        raise AssetError(
            "游戏正在运行，无法新增资产",
            hint="游戏占着游戏目录里的文件时写入会失败（WinError 5）。请先退出游戏。",
        )
    if target.exists():
        raise AssetError(
            f"落点已经存在：{target}",
            hint="这个键派生出来的包名撞上了现场的包 ⇒ 先 newasset restore，或换一个 --key。",
        )
    report = BundleAddReport(plan=plan, dry_run=False)
    label = f"资产落包 {plan.key}"
    store = rd.PatchStore(rd.patch_store_root(project), game_root=root)
    recorder = store.begin(label)
    recorder.capture_file(plan.catalog_path, before=plan.catalog_before, after=plan.catalog_after)
    # catalog.hash 与 catalog 是**一对**：同一条补丁里一起记、一起撤（别拆成两笔）。
    # ⚠ before 传 `or None`：盘上原本没有这个文件时，补丁柜的口径是 None = 还原就删掉它。
    recorder.capture_file(
        plan.catalog_hash_path,
        before=plan.catalog_hash_before or None,
        after=plan.catalog_hash_after,
    )
    recorder.capture_file(target, before=None, after=plan.bundle_data)
    recorder.commit()
    report.manifest = _write_bundle_manifest(plan)

    try:
        _write_verified(plan.catalog_path, plan.catalog_after, what="catalog")
        report.catalog_sha256 = sha256_of(plan.catalog_after)
        _write_verified(plan.catalog_hash_path, plan.catalog_hash_after, what="catalog.hash")
        _write_verified(target, plan.bundle_data, what="新包")
        with ufs.UfsReader(target) as reader:
            stream = bytes(reader.read_range(0, reader.total_uncompressed))
            file_size = reader.file_size
        got_crc = zlib.crc32(stream) & 0xFFFFFFFF
        if got_crc != plan.bundle_crc32 or file_size != len(plan.bundle_data):
            raise AssetError(
                f"新包写后复核不通过：crc32 {got_crc}（期望 {plan.bundle_crc32}）/ "
                f"文件长 {file_size:,}（期望 {len(plan.bundle_data):,}）"
            )
        report.bundle_sha256 = sha256_of(plan.bundle_data)
        report.zip_sha256 = _assert_zip_untouched(plan)
    except BaseException as exc:  # 出事必须用补丁撤回，且**不许吞**（所以捕 BaseException）
        done, undone = _undo(store)
        if done:
            manifest_path(project).unlink(missing_ok=True)
        hint = exc.hint if isinstance(exc, AssetError) else None
        raise AssetError(
            f"落包 {plan.key} 失败：{exc}（{undone}）",
            hint=hint or "看上一句撤销结果；补丁还在的话可以再跑一次 newasset restore。",
        ) from exc

    if store.json_path.is_file() and store.blob_path.is_file():
        report.patch_bytes = store.json_path.stat().st_size + store.blob_path.stat().st_size
    report.verified = True
    return report


def run_add_bundle_dry(plan: BundleAddPlan) -> BundleAddReport:
    """干跑的读数（不再读盘：计划里就有全部数字）。"""
    return BundleAddReport(plan=plan, dry_run=True)

def _patch_restore_hint(root: Path) -> str:
    """DLL 那条给用户的可执行下一步（⛔ 不许只印状态了事）。"""
    return (
        "要撤它自己跑 .venv\\Scripts\\python.exe -m ba_mod_maker.cli patch restore "
        f"--game {root}"
    )


def _dll_line(
    root: Path,
    *,
    manifest: dict[str, Any] | None,
    keep_dll: bool,
    check_game_running: bool,
) -> str:
    """DLL 那一条的措辞（Lead 2026-10-06 定）：**现场读数说了算，清单纯参考**。

    1. 已经 == .orig（sha256 等于备份）⇒ 印「已是原版，无需还原」，**不报错、也不动它**；
    2. 现场是补丁态**且**清单说那次是我们打的 ⇒ 才真去还原；
    3. 现场是补丁态但不是本单打的（清单丢了 / 标记 false）⇒ 保持原样 ＋ 一条可执行的 hint；
    4. 现场被别的工具改过 / 没有备份 ⇒ 不猜，印状态 ＋ 同样的 hint。
    """
    status = dllpatch.status(root)
    if keep_dll:
        return f"  DLL：按 --keep-dll 保留现场（{_patch_restore_hint(root)}）"
    if status.state is dllpatch.PatchState.MISSING:
        return f"  DLL：找不到 {status.dll}（没法核对；--game 指错？）"
    if status.state is dllpatch.PatchState.NOT_INSTALLED:
        return "  DLL：已是原版，无需还原（sha256 == 备份）"
    if status.state is dllpatch.PatchState.PATCHED:
        if manifest is None:
            why = "认不出是不是本单打的（清单不在了）"
        elif not manifest.get("dll_applied"):
            why = f"装之前它就已经是「{manifest.get('dll_state_before', '?')}」，不是本单打的"
        else:
            return "  DLL：" + dllpatch.restore(root, check_game_running=check_game_running)
        return f"  DLL：保持原样 —— {why}；{_patch_restore_hint(root)}"
    return (
        f"  DLL：保持原样 —— 现场是「{status.state.label}」，不是本单能撤的样子；"
        + _patch_restore_hint(root)
    )


def restore(
    *,
    game_root: str | Path,
    project_root: str | Path,
    keep_dll: bool = False,
    check_game_running: bool = True,
) -> RestoreOutcome:
    """一条命令全撤：补丁柜逐位还原（catalog / BA_Mod.zip / 删掉我们加的包）＋ 本单打的 DLL。

    * **幂等**：补丁柜里没有未还原的补丁时不报错（「撤销」点几次都该一样）；
    * **DLL 只撤本单打的那一次**，且**现场读数优先**（见 :func:_dll_line）：
      已经 == .orig（sha256 等于备份）⇒ 印「已是原版，无需还原」并跳过（清单只作参考，不当真理）；
      补丁态但不是本单打的（清单丢了 / 标记 false）⇒ 保持原样 ＋ 一条 hint 叫你去 patch restore，
      ⛔ 绝不静默；
      ⛔ 绝不无条件还原 —— 那会把用户自己的补丁撤掉（Lead 2026-10-06 定）；
    * 补丁**没撤干净**时 DLL 也不动（先解决上一条，再跑一次 restore）。
    """
    root, project = Path(game_root), Path(project_root)
    if check_game_running and game_mod.is_game_running():
        raise AssetError(
            "游戏正在运行，无法还原资产",
            hint="游戏占着游戏目录里的文件时写入会失败（WinError 5）。请先退出游戏。",
        )
    store = rd.PatchStore(rd.patch_store_root(project), game_root=root)
    manifest = _read_manifest(project)
    lines: list[str] = []
    ok = True
    if store.has_pending():
        report = store.restore()
        ok = report.ok
        lines.extend(report.lines())
    else:
        lines.append(f"  补丁柜里没有未还原的补丁：{store.root}（无需还原）")

    if not ok:
        dll_line = "  DLL：**没动**（补丁没撤干净 ⇒ 先解决上一条，再跑一次 restore）"
    else:
        dll_line = _dll_line(
            root, manifest=manifest, keep_dll=keep_dll, check_game_running=check_game_running
        )
    lines.append(dll_line)
    if ok:
        manifest_path(project).unlink(missing_ok=True)
    return RestoreOutcome(ok=ok, lines_=tuple(lines), dll_line=dll_line)


# --------------------------------------------------------------------------- #
# 改指向（repoint）—— 产品操作集里的「**修改**」：只改 `Units.ModelFileName` 一格
# --------------------------------------------------------------------------- #
def _catalog_counts(raw: bytes) -> tuple[int, int, int]:
    """(键, 桶, 条目) —— 口径唯一在 `catalog_bin_write.counts`，这里只转手。"""
    from ba_mod_maker.core.assets import catalog_bin_write as cbw

    return cbw.counts(raw)


@dataclass(slots=True)
class RepointPlan:
    """一次「改指向」的完整计划（⛔ 不新增 catalog 条目、⛔ 不碰包、⛔ 不碰 DLL）。"""

    game_root: Path
    project_root: Path
    zip_path: Path
    zip_before: bytes
    zip_after: bytes
    row_index: int
    row_id: int
    row_name: str
    row_hud: str
    old_key: str
    new_key: str
    #: 目标键在 catalog 里解析出来的资产路径（必须是 `.prefab`）。
    asset_path: str
    table_count: int
    catalog_path: Path
    #: 装前 catalog 的 (键, 桶, 条目) 读数 —— 装后必须**一个都没多**。
    catalog_counts: tuple[int, int, int]

    @property
    def zip_sha256_before(self) -> str:
        return hashlib.sha256(self.zip_before).hexdigest()

    @property
    def zip_sha256_after(self) -> str:
        return hashlib.sha256(self.zip_after).hexdigest()

    def restore_command(self) -> str:
        return (
            ".venv\\Scripts\\python.exe -m ba_mod_maker.cli newasset restore "
            f"--game {self.game_root} --project-root {self.project_root}"
        )

    def plan_lines(self) -> tuple[str, ...]:
        keys, buckets, entries = self.catalog_counts
        return (
            "[改指向] 计划（只改 Units.ModelFileName 一格）",
            f"  游戏目录 : {self.game_root}",
            f"  工程目录 : {self.project_root}",
            f"  行       : {self.row_hud or self.row_name}"
            f"（Id={self.row_id} · 行下标 {self.row_index}）",
            f"  指向     : {self.old_key!r} -> {self.new_key!r}",
            f"  资产路径 : {self.asset_path}（catalog 里**已经登记**）",
            "① catalog：**本命令不动**",
            f"  {self.catalog_path}",
            f"  读数 键 {keys:,} / 桶 {buckets:,} / 条目 {entries:,}（装后必须一个都没多）",
            "② 数据库：只改这一格",
            f"  {self.zip_path}",
            f"  BA_Mod.zip {len(self.zip_before):,} -> {len(self.zip_after):,} B"
            f"（{len(self.zip_after) - len(self.zip_before):+,}）",
            "③ 包 / DLL：本命令**都不碰**",
            "撤",
            f"  {self.restore_command()}",
        )


def plan_repoint(
    *,
    unit: str,
    key: str,
    game_root: str | Path,
    project_root: str | Path,
    row_id: int | None = None,
) -> RepointPlan:
    """算一次「改指向」：把 Units 那一行的 `ModelFileName` 指到**已登记**的键。

    三条 fail-closed（都有用例）：

    1. 目标键必须**已经在 catalog 里**、且解析到 `.prefab`（模型地址）——
       本动作**不新增条目**，所以它不是「装一个新包」的替代品；
    2. 命中的行必须唯一（`ModelFileName` 实测有 13 个重名 ⇒ 不唯一就要 `--id`）；
    3. 这一行现在指的就是那个键 ⇒ 拒（没有要改的东西）。

    为什么需要它（2026-10-22，P1）：Steam 校验完整性之后 `Units.ModelFileName` 回到
    真件键、catalog 里我们那些键也被抹掉；想把某行指回**仍然登记着**的旧键（例 A2BR4）
    原先**没有任何入口** —— 只能「重产一个内容不同的包」绕，那是新增、不是修改
    （AGENTS §0.10：产品操作集 = 新增 / 删除 / **修改**）。
    """
    root = Path(game_root)
    project = Path(project_root)
    if not game_mod.is_game_root(root):
        raise AssetError(
            f"这里不像游戏根目录（缺 BrokenArrow_Data\\StreamingAssets\\aa）：{root}",
            hint="--game 指游戏安装目录（含 BrokenArrow.exe 那一层）。",
        )
    validated = validate_key(key)
    zip_path = external_db.game_zip_path(root)
    if not zip_path.is_file():
        raise AssetError(f"游戏根里没有 {zip_path.name}：{zip_path}", hint=_zip_missing_hint(root))
    catalog_path = game_mod.catalog_file(root)
    catalog_raw = catalog_path.read_bytes()
    from ba_mod_maker.core.assets import catalog_bin_write as cbw

    asset_path = cbw.asset_path_of_key(catalog_raw, validated)
    if asset_path is None:
        raise AssetError(
            f"catalog 里没有可用的资产地址键 {validated!r}",
            hint=(
                "这一档**只改指向、不新增条目** ⇒ 目标键必须已经登记过"
                "（例 A2BR4 -> Assets/Resources_moved/BAMod/RU_T72B_Base.prefab）。"
                "要新造一条地址走 pack install / newasset add-model。"
            ),
        )
    if not asset_path.lower().endswith(".prefab"):
        raise AssetError(
            f"键 {validated!r} 解析到的不是模型地址：{asset_path}",
            hint=("ModelFileName 只能指 prefab（真件 RU_T72B -> "
                  "Assets/Resources_moved/ModelPrefabs/RU/RU_T72B_Base.prefab）；"
                  "图标那一列是 ThumbnailFileName，⛔ 别写混。"),
        )
    zip_before = zip_path.read_bytes()
    edit = rewrite_zip_row(
        zip_path, unit=unit, key=validated, row_id=row_id, column="ModelFileName"
    )
    return RepointPlan(
        game_root=root,
        project_root=project,
        zip_path=zip_path,
        zip_before=zip_before,
        zip_after=edit.zip_after,
        row_index=edit.row_index,
        row_id=edit.row_id,
        row_name=edit.row_name,
        row_hud=edit.row_hud,
        old_key=edit.old_key,
        new_key=validated,
        asset_path=asset_path,
        table_count=edit.table_count,
        catalog_path=catalog_path,
        catalog_counts=_catalog_counts(catalog_raw),
    )


@dataclass(slots=True)
class RepointReport:
    """真改（或干跑）的读数。写后复核读的都是**盘上那份**，不是计划里的数。"""

    plan: RepointPlan
    dry_run: bool
    zip_sha256: str = ""
    install_verified: bool = False
    verified: bool = False
    patch_bytes: int = 0
    patch_path: Path | None = None
    catalog_counts_after: tuple[int, int, int] = ()

    def lines(self) -> tuple[str, ...]:
        plan = self.plan
        head = "[改指向] 干跑（一个字节都没写）" if self.dry_run else "[改指向] 完成"
        out = [head, *plan.plan_lines()[1:]]
        if self.dry_run:
            return tuple(out)
        keys, buckets, entries = plan.catalog_counts
        after = self.catalog_counts_after
        out.extend((
            "",
            "读数（写后复核，读的是**盘上那份**）",
            f"  Units[{plan.row_index}].ModelFileName = {plan.new_key!r}",
            f"  catalog 键 {keys:,} / 桶 {buckets:,} / 条目 {entries:,}"
            f" -> {after[0]:,} / {after[1]:,} / {after[2]:,}（⛔ 一个都没新增）",
            f"  BA_Mod.zip sha256 {plan.zip_sha256_before[:16]}… -> {self.zip_sha256[:16]}…",
            f"  补丁柜 {self.patch_path or '（无）'} · {self.patch_bytes:,} B",
        ))
        return tuple(out)


def run_repoint_dry(plan: RepointPlan) -> RepointReport:
    """干跑的读数（不再读盘：计划里就有全部数字；游戏目录 / 工程目录零写入）。"""
    return RepointReport(plan=plan, dry_run=True)


def run_repoint(plan: RepointPlan, *, check_game_running: bool = True) -> RepointReport:
    """真改：`BA_Mod.zip` 走反向 Δ 补丁柜落盘，然后**读回盘上那份**核对两条判据。

    判据（这一档的「不新增条目」就落在第 ② 条）：
    ① `Units[row].ModelFileName` 读回来 == 新键；
    ② catalog 的 (键, 桶, 条目) 读数与装前**逐项相同**。
    """
    root, project = plan.game_root, plan.project_root
    if check_game_running and game_mod.is_game_running():
        raise AssetError(
            "游戏正在运行，无法改指向",
            hint="游戏占着游戏目录里的文件时写入会失败（WinError 5）。请先退出游戏。",
        )
    label = f"改指向 {plan.old_key} -> {plan.new_key}（Units[{plan.row_index}]）"
    with _work_dir() as folder:
        work_zip = folder / WORK_ZIP_NAME
        work_zip.write_bytes(plan.zip_after)
        install = external_db.install(
            root, work_zip, project_root=project, label=label,
            check_game_running=check_game_running,
        )
    report = RepointReport(
        plan=plan,
        dry_run=False,
        zip_sha256=install.sha_after,
        install_verified=install.verified,
        patch_bytes=install.patch_bytes,
        patch_path=install.patch_path,
    )
    package = dbzip.DbZip.read(plan.zip_path)
    raw_units = package.tables.get("Units")
    if raw_units is None:
        raise AssetError(f"装完的 {plan.zip_path.name} 里没有 Units 表")
    rows = _rows_of(json.loads(raw_units.decode("utf-8")))
    got = str(rows[plan.row_index].get("ModelFileName", ""))
    if got != plan.new_key:
        raise AssetError(
            f"写后复核不通过：Units[{plan.row_index}].ModelFileName 读回来是 {got!r}"
            f"（期望 {plan.new_key!r}）",
            hint="那一格没落盘 —— 别继续，先 newasset restore 退回装前再看。",
        )
    report.catalog_counts_after = _catalog_counts(plan.catalog_path.read_bytes())
    if tuple(report.catalog_counts_after) != tuple(plan.catalog_counts):
        raise AssetError(
            "写后复核不通过：catalog 的条目读数变了（这一档**不许**新增 / 删除条目）"
            f"：{plan.catalog_counts} -> {report.catalog_counts_after}",
            hint="先 newasset restore 退回装前，再把这份读数报上来。",
        )
    report.verified = True
    return report
