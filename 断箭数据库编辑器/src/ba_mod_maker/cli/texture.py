"""`texture` 子命令：**把一张 PNG 的像素带进一个件包**（三条：encode / build / apply）。

    ba-mod-maker texture encode <png> [--out <bin>] [--format bc7] [--mips N] [--json]
    ba-mod-maker texture build  --png <图> --source <真包> --asset-path <容器路径>
                              [--format bc7] [--mips N] [--out <新包>] [--json]
    ba-mod-maker texture apply --bundle <件包> --material <材质名> --slot <槽名>
                              --png <图> --out <新包> [--victim <pathID>]
                              [--alpha opaque|keep] [--mips N] [--json]

## encode 的 `--format`（L5 接的 BC7）

不给 `--format` 时走老路（BC3 / DXT5，逐字节不变）；给了 `--format bc7|bc4|bc1|bc3`
就走 `asset_kernel/texture_encode.py` —— **etcpak 现编**，BC7 就是 Unity 的
`m_TextureFormat = 25`。⚠ BC4 **只保 R 通道**（单通道 Mask 图走它），喂彩色图会丢 G/B，
所以那种情况下会印一行大字。Crunched（28/29）**编不出来**（Python 只有解码器）⇒ 当场拒。

## build：从一张新图**从头造一张流式 Texture2D 小包**（L5）

类型树**从 `--source` 那个真包逐字节照抄**（⛔ 不造 schema），像素进本包 `.resS`，
`m_TextureFormat` 按 `--format` 写。**不给 `--out` = 干跑**（只编码 ＋ 造包，不落盘、不读回）；
给了 `--out` ⇒ 写盘之后**自己再开一遍出包读回六个读数**，任何一条对不上 ⇒ rc=2。

⛔ 不碰游戏目录（装机归 `newasset add-model`）· 只写 `--out` · 源包只读。

## 退出码（也写在 `--help` 里）

| 码 | 什么时候 |
|---|---|
| 0 | 出包了（build：干跑也算），且这个包里**没有**纹素落在包外的流式贴图（搬得动的都搬进来了） |
| 1 | 出包了，但**结果不干净**：还有流式贴图的纹素**搬不动**（源包里也没有那段字节 / 指着别的包），
|   | **逐张列原因**——⛔ 不静默算绿 |
| 2 | 用不了：参数不合法 / 包读不开 / 材质找不到 / 尺寸或 mip 不合法 / `--alpha keep` 被门控拒绝 /
|   | **build 的出包读回没过**（六个读数里有对不上的）/ 格式是 Crunched（编不出来） |

## ★ 尺寸 / 格式 / mip 变了也收（2026-10-11 变长档）

PNG 的宽 / 高**不再要求与受害者相同**，mip 数也能自己定（`--mips`）。这时贴图那六个数
（`m_Width` / `m_Height` / `m_MipCount` / `m_TextureFormat` / `m_CompleteImageSize` /
`m_StreamData{path,offset,size}`）**一起落位**，新像素照旧只进本包 `.resS`。
⛔ 工单 §7 坑 2：**只改 `m_TextureFormat`、把长度留旧值 = 必坏** —— 所以落盘之后产品会
**自己再开一遍出包**读回六个读数（`.resS` 节点 size 与逐字节像素也在内），任何一条对不上
就当场抛（`asset_kernel/texture_install.py::verify_texture_out`）。

mip 数怎么定：不给 `--mips` 时 —— 尺寸与受害者**相同** ⇒ 照抄受害者的 mip 数（等长档的字节
因此逐字节不变）；尺寸**不同** ⇒ 满链（逐级编到 1x1，例 64x64 -> 7 级、128x48 -> 8 级）。

## ★ alpha 门控（知识长在代码路径上）

`_work/texd/读数.md` §10 那条真机兑现的坑（斜坡 alpha 图 ⇒ 车身 **50.20%** 的面被裁成洞）——
判定与动作都在 `asset_kernel/texture_install.py` 里，这里只把它的两行读数印出来。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ba_mod_maker.cli.emit import Emitter
from ba_mod_maker.core.errors import BaModMakerError

#: 退出码（与 finder/validator 同一套语义：0 完成 / 1 结果不干净 / 2 用不了）。
EXIT_OK = 0
EXIT_DIRTY = 1
EXIT_UNUSABLE = 2

_DESCRIPTION = """把一张 PNG 的像素带进一个件包（PNG → BC7/BC3 ＋ 满 mip → .resS → 材质槽）。

  texture encode <png> [--out <bin>] [--format bc7] [--mips N]   只编码（不碰包）
  texture build  --png <图> --source <真包> --asset-path <容器路径>
                 [--format bc7] [--mips N] [--out <新包>]        新图 -> 流式贴图小包
  texture apply  --bundle <件包> --material <名> --png <图> --out <新包>
                 [--slot <槽名>] [--victim <pathID>] [--alpha opaque|keep]
                 [--asset-path <容器地址>] [--mips N]

贴图的**尺寸 / 格式 / mip 数允许与受害者不同**（变长档）—— 这时 m_Width / m_Height /
m_MipCount / m_TextureFormat / m_CompleteImageSize / m_StreamData{path,offset,size}
六个数**一起落位**，新像素只进本包 .resS。⛔ 新增对象与层级不动；源包只读，只有 --out 被写。
落盘之后产品自己读回出包复核一遍（六读数逐条对，见 verify_texture_out）。

退出码：
  0  出包了，且包里每张流式贴图的纹素都在本包 .resS 里（闭包里搬得动的那几张已经搬过来）
  1  出包了，但还有流式贴图的纹素**搬不动**（源包里也没有那段字节 / 指着别的包）—— 逐张印原因
  2  用不了（参数 / 包 / 材质 / 尺寸 / mip / --alpha keep 被拒 / 出包复核没过）
"""

_EPILOG = r"""--asset-path（哪个 prefab）：
  同一颗材质常被机身 + 炮塔共用（实测 'DLC2_CV9035 1' 被 CV9035 / MK3 / MK4 三个 prefab 用）。
  只命中一个 ⇒ 自动用它；命中多个 ⇒ **拒绝并列出全部候选**（⛔ 不猜 —— 第一版随便挑了一个，
  直接产出了 MK4 炮塔的件包）。

mip 数（--mips）：
  不给：尺寸与受害者相同就照抄受害者的，尺寸不同就用满链（逐级编到 1x1）。
  给了：按给的（必须 <= 满链；⛔ Unity 没有比 1x1 更小的级）。
  ⚠ 改了 mip 数，m_CompleteImageSize 与 m_StreamData.size 就跟着改 —— 命令会一起写掉。

alpha 门控（真机兑现过的坑）：
  目标材质 _ENABLEALPHACLIPPING == 1 时，图里 alpha < _AlphaCutoff*255 的那些面会被裁成**洞**。
  默认（--alpha opaque）**自动把 alpha 补成全 255**（RGB 一个字节不动）并印一行大字；
  --alpha keep 则是"我就要原样" —— 门控开着且确有会被裁的像素时**当场拒绝**（不会偷偷放行）。
  _ENABLEALPHACLIPPING != 1 的材质不裁 ⇒ 不动 alpha，两行读数照样印。

例：
  ba-mod-maker texture encode 素材\车身.png --out 素材\车身.bc3
  ba-mod-maker texture apply --bundle 件包.bundle --material "DLC2_CV9035 1" --slot Layer_A97CDC25 ^
      --png 素材\车身.png --out 件包-带图.bundle --victim 3809638870875903096
"""


def te_format_names() -> tuple[str, ...]:
    """命令行认的格式名（含 Crunched 那几个 —— 敲了会得到**带理由的拒绝**，不是不认识）。"""
    from ba_mod_maker.asset_kernel import texture_encode as te

    return tuple(sorted(te.CRUNCHED_NAMES))


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """注册 `texture` 子命令（`__main__.py` 只调这一句 ＋ 把 `run` 挂进 handlers）。"""
    parser = sub.add_parser(
        "texture",
        help="贴图进件包（encode / apply；退出码 0 完成 / 1 结果不干净 / 2 用不了）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("action", choices=("encode", "build", "reencode", "apply", "dump"),
                        help="要做什么：encode 编码 / build 新图造流式小包（L5） / "
                             "reencode 真件包里的一张真贴图换个格式重编（L5） / apply 进包")
    parser.add_argument("png_pos", nargs="?", metavar="png", help="encode 用：要编码的 PNG")
    parser.add_argument("--png", dest="png_opt", help="apply 用：要带进包的 PNG")
    parser.add_argument("--out", help="encode：载荷落点（不给不写）／ build：新包落点"
                                             "（**不给 = 干跑**）／ apply：新包落点（必给）")
    parser.add_argument(
        "--format",
        choices=(("bc1", "bc3", "bc4", "bc7") + tuple(sorted(te_format_names()))),
        default=None,
                        help="encode / build 用：块压缩格式（不给：encode 走老路 BC3；"
                             "build 走 BC7）。BC7 = Unity m_TextureFormat 25；"
                             "BC4 只保 R 通道（单通道 Mask 图走它）；"
                             "Crunched 编不出来（Python 只有解码器）")
    parser.add_argument("--source", help="build 用：**真包**（类型树从它逐字节照抄）")
    parser.add_argument("--path-id", type=int,
                        help="reencode 用：包里那张 Texture2D 的 pathID（texture survey 列）")
    parser.add_argument("--project-root", help="reencode 用：工程目录（反向 Δ 落 <工程>/patches/）")
    parser.add_argument(
        "--unit",
        help="dump 用：单位键（= Units.ModelFileName，例 DLC3_Yak130）—— 导出它用到的每一张贴图",
    )
    parser.add_argument(
        "--only-material",
        action="append",
        default=[],
        help="dump 用：只导这些材质（可重复；不给 = 该件每一颗材质）",
    )
    parser.add_argument("--sheet", action="store_true", help="dump 用：再拼一张接触表（sheet.png）")
    parser.add_argument(
        "--i-know-game-dir",
        action="store_true",
        help="★ 默认不给：--out 落在游戏根之内**一律拒**（rc=2）—— 本产品只写工程目录，"
             "装机归 `newasset add-model`。确实要往游戏目录写才加它",
    )
    parser.add_argument("--bundle-name", help="build 用：新包名（不给：按资产路径推）")
    parser.add_argument("--name", help="build 用：Texture2D 的 m_Name（不给：按资产路径推）")
    parser.add_argument("--color-space", type=int, default=1,
                        help="build 用：m_ColorSpace（1 = sRGB，默认；法线/Mask 那种给 0）")
    parser.add_argument("--bundle", help="apply 用：源件包（**只读**）")
    parser.add_argument("--material", help="apply 用：材质名（包内 m_Name，必须唯一）")
    parser.add_argument(
        "--slot",
        help="apply 用：只指这一个槽（例 Layer_A97CDC25）。**不给就指该材质的全部颜色层**"
             "（读 shader 的 desc：Albedo / Camo）；只指一个而还有别的颜色层会印一条警告",
    )
    parser.add_argument(
        "--asset-path",
        help="apply 用：容器里那个 prefab 的地址（与 newasset add-model / build-model 同义）。"
             "不给：只命中一个就自动用，命中多个就拒绝并列候选；"
             "build 用：新 Texture2D 的容器路径（= catalog 的 internalId，Assets/...）",
    )
    parser.add_argument(
        "--victim",
        type=int,
        help="apply 用：接收我们字节的那张贴图 pathID（默认 = 槽现在指向的那张）",
    )
    parser.add_argument(
        "--alpha",
        choices=("opaque", "keep"),
        default="opaque",
        help="opaque（默认）：门控开着就把 alpha 补成全 255；keep：原样 —— 会被裁就当场拒绝",
    )
    parser.add_argument(
        "--mips",
        type=int,
        default=None,
        help="encode / build / apply 用：mip 级数。encode / build 不给 = **满链**"
             "（逐级编到 1x1）；apply 不给 = 尺寸与受害者相同就照抄受害者的，不同就用满链。"
             "必须 <= 满链",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="只输出一个 JSON 文档（人读那份改走 stderr）",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """跑一遍。返回退出码（0 / 1 / 2）。"""
    emitter = Emitter(json_mode=bool(getattr(args, "json_out", False)))
    action = str(getattr(args, "action", "") or "")
    png = getattr(args, "png_opt", None) or getattr(args, "png_pos", None)
    try:
        if action == "dump":
            return _run_dump(
                emitter,
                str(getattr(args, "unit", "") or ""),
                getattr(args, "out", None),
                tuple(getattr(args, "only_material", ()) or ()),
                bool(getattr(args, "sheet", False)),
            )
        if action == "encode":
            return _run_encode(
                emitter, png, getattr(args, "out", None),
                getattr(args, "format", None), getattr(args, "mips", None),
            )
        if action == "build":
            return _run_build(emitter, args, png)
        if action == "reencode":
            return _run_reencode(emitter, args)
        if action == "apply":
            return _run_apply(emitter, args, png)
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    except OSError as exc:
        emitter.failure(f"读写失败：{exc}", hint="看路径对不对、文件是不是被别的进程占着")
        return EXIT_UNUSABLE
    emitter.failure("要 texture encode / texture apply（--help 看用法）")
    return EXIT_UNUSABLE




def _safe(name: str) -> str:
    """文件名里不许出现 Windows 保留字符（材质名里常见空格与 `/`）。"""
    out = "".join(ch if ch.isalnum() or ch in " ._-" else "_" for ch in name).strip()
    return out or "unnamed"


def _run_dump(
    emitter: Emitter,
    unit: str,
    out_dir: Any,
    only_materials: tuple[str, ...],
    sheet: bool,
) -> int:
    """把一件单位用到的**每一张贴图**导成 PNG ＋ `manifest.json`（只读；只写 `--out` 目录）。"""
    if not unit:
        emitter.line("dump 需要 --unit <单位键>（例 DLC3_Yak130）")
        return EXIT_UNUSABLE
    if not out_dir:
        emitter.line("dump 需要 --out <目录>")
        return EXIT_UNUSABLE
    from PIL import Image

    from ba_mod_maker.asset_kernel.texture_dump import dump_unit_textures
    from ba_mod_maker.core.assets.game import find_game_root

    out = Path(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    try:
        report = dump_unit_textures(
            find_game_root(), unit, only_materials=only_materials
        )
    except Exception as exc:  # noqa: BLE001 -- CLI 边界：把原因原样印出来
        emitter.line(f"导不出来：{type(exc).__name__}: {exc}")
        return EXIT_UNUSABLE
    emitter.line(f"单位 {report.unit_key} · 包 {Path(report.bundle).name}")
    names = '; '.join(report.materials)
    emitter.line(f"材质 {len(report.materials)} 颗：{names}")
    manifest = []
    for row in report.rows:
        manifest.append({
            "material": row.material,
            "slot": row.slot,
            "texture_id": row.texture_id,
            "texture_name": row.texture_name,
            "width": row.width,
            "height": row.height,
            "format": row.format_name,
            "color_space": row.color_space,
            "sha256": row.sha256,
            "missing": row.missing,
            "note": row.note,
        })
        if row.missing:
            emitter.line(f"  ⛔ {row.material} :: {row.slot} —— {row.note}")
            continue
        name = f"{_safe(row.material)}__{_safe(row.slot)}.png"
        image = Image.frombytes("RGBA", (row.width, row.height), row.rgba)
        image.save(out / name)
        emitter.line(f"  ✅ {row.material} :: {row.slot} -> {name} ({row.width}x{row.height})")
    (out / "manifest.json").write_text(
        json.dumps(
            {"unit": report.unit_key, "asset": report.asset_path, "rows": manifest},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    emitter.line(f"manifest -> {out / 'manifest.json'}")
    if sheet:
        _write_sheet(out, [row for row in report.rows if not row.missing])
    emitter.line(f"导出 {report.dumped} 张（缺 {len(report.rows) - report.dumped} 张）")
    return EXIT_OK if report.dumped else EXIT_DIRTY


def _write_sheet(out: Path, rows: Any, cell: int = 360) -> None:
    """拼一张接触表（每张图缩到 `cell`，左上角印「材质 :: 槽名」）。"""
    from PIL import Image, ImageDraw

    if not rows:
        return
    cols = 4
    lines = (len(rows) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * cell, lines * (cell + 24)), (24, 24, 28))
    draw = ImageDraw.Draw(canvas)
    for index, row in enumerate(rows):
        name = f"{_safe(row.material)}__{_safe(row.slot)}.png"
        image = Image.open(out / name).convert("RGBA")
        image.thumbnail((cell - 8, cell - 8))
        x = (index % cols) * cell
        y = (index // cols) * (cell + 24)
        canvas.paste(image, (x + 4, y + 20), image)
        draw.text((x + 6, y + 4), f"{row.material} :: {row.slot}"[:60], fill=(230, 230, 230))
    canvas.save(out / "sheet.png")


def _run_encode(
    emitter: Emitter,
    png: str | None,
    out: str | None,
    fmt_name: str | None = None,
    mips: int | None = None,
) -> int:
    """`texture encode`：不给 `--format/--mips` 走老路（BC3，逐字节不变）；

    给了就走 `asset_kernel/texture_encode.py` —— **etcpak 现编**（BC7 = m_TextureFormat 25）。
    """
    from ba_mod_maker.asset_kernel import texture_install as ti

    if not png:
        emitter.failure("texture encode 要一个 PNG 路径",
                        hint="例：texture encode 素材\\图.png --out 素材\\图.bc3")
        return EXIT_UNUSABLE
    if fmt_name or mips is not None:
        return _run_encode_blocks(emitter, png, out, fmt_name, mips)
    report = ti.encode_png_to_payload(png)
    target = Path(out) if out else None
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(report.payload)
        report = ti.EncodeReport(png=report.png, image=report.image, mip_count=report.mip_count,
                                 payload=report.payload, out=target, note=report.note)
    for line in report.lines():
        emitter.line(line)
    emitter.document({
        "action": "encode", "png": str(report.png), "width": report.image.width,
        "height": report.image.height, "format": ti.DXT5, "mip_count": report.mip_count,
        "payload_bytes": report.payload_bytes, "payload_sha256": report.payload_sha256,
        "out": str(target) if target else None,
        "alpha_gate": {"applicable": False, "reason": "encode 不看材质（n/a）",
                       "below": report.note.below, "total": report.note.total,
                       "ratio": round(report.note.ratio, 6),
                       "min": report.note.amin, "max": report.note.amax},
    })
    return EXIT_OK


def _run_reencode(emitter: Emitter, args: argparse.Namespace) -> int:
    """`texture reencode`：把**真件包里的一张真贴图**按另一个格式重编（L5）。

    等长外科手术（`writer.write_objects` + `writer.write_stream`）：只重压命中的那几个块，
    反向 Δ 由产品柜子给出（实测 73,319 B / 整包 2,220,004 B = 3.3%）。
    源包只读；--out 落在游戏目录里一律拒（--i-know-game-dir 才放行）。
    """
    from ba_mod_maker.asset_kernel import texture_encode as te

    missing = [
        name
        for name, value in (
            ("--bundle", getattr(args, "bundle", None)),
            ("--path-id", getattr(args, "path_id", None)),
        )
        if value is None
    ]
    if missing:
        emitter.failure(
            "texture reencode 缺参数：" + "、".join(missing),
            hint="例：texture reencode --bundle unitmodifications_....bundle "
                 "--path-id -8977521196609468022 --format bc7 "
                 "--out 改包.bundle --project-root <工程>",
        )
        return EXIT_UNUSABLE
    fmt = te.format_of_name(getattr(args, "format", None) or "bc7")
    plan = te.reencode_in_bundle(
        args.bundle,
        path_id=int(args.path_id),
        out=getattr(args, "out", None),
        format_id=fmt,
        project_root=getattr(args, "project_root", None),
        allow_game_dir=bool(getattr(args, "i_know_game_dir", False)),
    )
    for line in plan.lines():
        emitter.line(line)
    if plan.out is None:
        emitter.line("  * 干跑：两条外科计划都算了，一个字节都没写（要写加 --out）")
    emitter.document(plan.document())
    return EXIT_OK if plan.ok else EXIT_UNUSABLE


def _channel_gate(emitter: Emitter, png: str, fmt: int) -> dict[str, Any]:
    """BC4 的**单通道门**：R/G/B 不同的像素数 —— 只印读数，⛔ 不改任何文件。

    BC4 **只保 R 通道**（真件 MaskMap 走它）⇒ 彩色图喂进去 G/B 当场丢。
    这条是**给人看的门**（不是判据）：CLI 在 `--format bc4` 且确有彩色像素时印一行大字。
    """
    from ba_mod_maker.asset_kernel import texture_encode as te
    from ba_mod_maker.asset_kernel import texture_install as ti

    image = ti.read_png(png)
    stats = te.channel_stats(image.pixels, image.width, image.height)
    total = max(1, int(stats["total"]))
    same = int(stats["same_rgb_pixels"])
    color = int(total - same)
    emitter.line(f"  单通道门  : R==G==B 的像素 {same:,} / {total:,} = {same / total:.2%}")
    if int(fmt) == te.BC4 and color:
        emitter.line(
            f"  X **BC4 只保 R 通道**：这张图有 {color:,} 个像素 R/G/B 不同"
            f"（{color / total:.2%}）=> 解回来 G/B 是 0。彩色图请用 --format bc7。"
        )
    return {"same_rgb_pixels": same, "total": total, "color_pixels": color,
            "applies": int(fmt) == te.BC4}


def _run_encode_blocks(
    emitter: Emitter, png: str, out: str | None, fmt_name: str | None, mips: int | None
) -> int:
    """`texture encode --format/--mips`：L5 那条（etcpak 现编块压缩）。"""
    from ba_mod_maker.asset_kernel import texture_encode as te

    fmt = te.format_of_name(fmt_name or "bc3")
    encoded = te.encode_png(png, format_id=fmt, mip_count=mips)
    target = Path(out) if out else None
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded.payload)
    for line in encoded.lines():
        emitter.line(line)
    gate = _channel_gate(emitter, png, fmt)
    document: dict[str, Any] = {
        "action": "encode",
        "png": str(png),
        "width": int(encoded.width),
        "height": int(encoded.height),
        "format": int(encoded.format),
        "format_name": te.format_name(encoded.format),
        "mip_count": int(encoded.mip_count),
        "payload_bytes": int(encoded.payload_bytes),
        "payload_sha256": encoded.sha256(),
        "out": str(target) if target else None,
        "channel_gate": gate,
    }
    emitter.document(document)
    return EXIT_OK


def _run_build(emitter: Emitter, args: argparse.Namespace, png: str | None) -> int:
    """`texture build`：一张新图 -> 一张**流式 Texture2D** 小包（L5）。

    ⛔ 只写 `--out`，源包只读；不给 `--out` = **干跑**（编码 ＋ 造包，不落盘、不读回）。
    """
    from ba_mod_maker.asset_kernel import texture_encode as te

    missing = [
        name for name, value in (
            ("--png", png),
            ("--source", getattr(args, "source", None)),
            ("--asset-path", getattr(args, "asset_path", None)),
        ) if not value
    ]
    if missing:
        emitter.failure(
            "texture build 缺参数：" + "、".join(missing),
            hint="例：texture build --png 图.png --source ammoicons_....bundle "
                 "--asset-path Assets/.../NEW.png --format bc7 --out 新包.bundle",
        )
        return EXIT_UNUSABLE
    fmt = te.format_of_name(getattr(args, "format", None) or "bc7")
    if getattr(args, "out", None):
        te.guard_out_path(args.out, allow_game_dir=bool(getattr(args, "i_know_game_dir", False)))
    plan = te.write_encoded_bundle(
        png=png,
        source=args.source,
        asset_path=args.asset_path,
        out=getattr(args, "out", None),
        format_id=fmt,
        mip_count=getattr(args, "mips", None),
        bundle_name=getattr(args, "bundle_name", None),
        name=getattr(args, "name", None),
        color_space=int(getattr(args, "color_space", 1) or 0),
    )
    for line in plan.lines():
        emitter.line(line)
    gate = _channel_gate(emitter, str(png), fmt)
    if plan.out is None:
        emitter.line("  * 干跑：没落盘，也没读回（要读回加 --out）")
    document = plan.document()
    document["channel_gate"] = gate
    emitter.document(document)
    return EXIT_OK if plan.ok else EXIT_UNUSABLE


def _run_apply(emitter: Emitter, args: argparse.Namespace, png: str | None) -> int:
    from ba_mod_maker.asset_kernel import texture_install as ti

    missing = [name for name, value in (("--bundle", args.bundle), ("--material", args.material),
                                        ("--png", png), ("--out", args.out))
               if not value]
    if missing:
        emitter.failure("texture apply 缺参数：" + "、".join(missing),
                        hint="例：texture apply --bundle 件包.bundle "
                             "--material 'DLC2_CV9035 1' --slot Layer_A97CDC25 "
                             "--png 图.png --out 新包.bundle")
        return EXIT_UNUSABLE
    from ba_mod_maker.asset_kernel import texture_encode as te

    te.guard_out_path(args.out, allow_game_dir=bool(getattr(args, "i_know_game_dir", False)))
    plan = ti.apply_texture(bundle=args.bundle, material=args.material,
                            slot=getattr(args, "slot", None), png=png,
                            out=args.out, victim=args.victim, alpha=args.alpha,
                            asset_path=getattr(args, "asset_path", None),
                            mips=getattr(args, "mips", None))
    for line in plan.lines():
        emitter.line(line)
    dirty = _foreign_textures(args.out)
    if dirty:
        emitter.line("")
        names = "、".join(f.name for f in dirty[:4])
        emitter.line(f"★ 结果不干净：出包里还有 {len(dirty)} 张流式贴图的纹素不在包里"
                     f"（{names}…）—— 装机后它们是白图。")
        for f in dirty[:8]:
            emitter.line(f"     ✗ {f.name}（{f.path_id}）：{f.reason}")
    doc = {**plan.document(),
           "dirty": {"foreign_textures": len(dirty),
                     "names": [f.name for f in dirty[:8]],
                     "reasons": [f.reason for f in dirty[:8]]}}
    emitter.document(doc)
    if dirty:
        return EXIT_DIRTY
    return EXIT_OK


def _foreign_textures(bundle: str | Path) -> list[Any]:
    """出包里"纹素不在包里"的流式贴图（判据**只有一份**：`texture_install.foreign_streams`）。

    ⚠ 那个判据比"节点名对不对"多一层**区间**检查：名字对上了、`offset + size` 超出节点长度
    的一样取不到纹素。⛔ 这条尺子读的是**出包**（不是计划里的自述）—— 两个来源对不上就有鬼。
    """
    from ba_mod_maker.asset_kernel import texture_install as ti

    return list(ti.foreign_streams(bundle))
