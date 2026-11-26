r"""把图标从游戏 bundle 里提取到本地缓存。

❗ **解析只有一份实现**：本工具直接调用 core/assets 的 IconResolver，
不自己写一套"按纹理名找图"的逻辑。

为什么这次要重写：旧版按 Texture2D 的**名字**取图，而 bundle 里实心图和
outline 空心线稿**同名**，于是 151 张图标被取成了线稿（用户报的 Konkurs 就是其一）。
实心/空心在 catalog 里是**两条独立地址**（裸键 vs outline 前缀），
只有按地址解析才不会撞车。

用法：
    .venv\Scripts\python.exe tools\extract_icons.py
    .venv\Scripts\python.exe tools\extract_icons.py --only unit_labels flags
    .venv\Scripts\python.exe tools\extract_icons.py --game "D:\Steam\...\broken_arrow"

输出：cache/icons/<family>/<sanitized key>.<png|jpg> ＋ _manifest.json

⚠️ 这个缓存现在是**兜底**：产品优先直接从 bundle 按需取图（见 docs/设计稿-图标管线.png），
只有在这台机器没装游戏时才回落到这里。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
from pathlib import Path

# 直接跑脚本时 src 不在 sys.path 上（没装成包也能用）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ba_mod_maker.core.assets import FAMILIES, IconFamily, find_game_root
from ba_mod_maker.core.assets.resolver import IconResolver
from ba_mod_maker.core.data import find_dataset_root, load_dataset

DEFAULT_OUT = Path("cache/icons")


def _sanitize(key: str) -> str:
    """地址键 -> 安全的文件名。反斜杠、空格、& 都会出问题。"""
    from ba_mod_maker.core.assets.families import sanitize

    return sanitize(key)


def _keys_of(instance, family: IconFamily) -> list[str]:
    """这一族的键：DB 的某一列（family.column 形如 表.列）**加上**族自己的常量键。

    常量键（family.keys）是"数据表里没有这一列"的那些族唯一的来源，
    例：目标类型图标来自 InfocardConfig 的 5 个 sprite 地址。
    """
    found: set[str] = set(family.keys)
    table_name, _, column = family.column.partition(".")
    if table_name and column:
        table = instance.table(table_name)
        index = table.column_index(column)
        found |= {r[index] for r in table.rows if isinstance(r[index], str) and r[index].strip()}
    return sorted(found)


def _save_image(image, target: Path) -> str:
    """存图。带 alpha 的存 PNG，不带的存 JPEG（立绘 433 张，PNG 会大到几百 MB）。"""
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_alpha:
        image.save(target.with_suffix(".png"), format="PNG", optimize=True)
        return target.with_suffix(".png").name
    image.convert("RGB").save(target.with_suffix(".jpg"), format="JPEG", quality=90)
    return target.with_suffix(".jpg").name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="提取军械库图标到本地缓存（兜底用）")
    parser.add_argument("--game", type=Path, default=None, help="BrokenArrow 安装根目录")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出目录")
    parser.add_argument("--only", nargs="*", default=None, help="只提取这几族（默认全部）")
    args = parser.parse_args(argv)

    game = args.game or find_game_root()
    if game is None:
        raise SystemExit(
            "找不到游戏目录。用 --game 指定 BrokenArrow 的安装根目录，"
            "或设环境变量 BA_MOD_MAKER_GAME。"
        )
    resolver = IconResolver(game)
    if not resolver.available:
        raise SystemExit(f"{game} 下没有 Addressables 的 bundle 目录，不像是游戏根目录。")

    root = find_dataset_root()
    if root is None:
        raise SystemExit("找不到 DataBaseCompiled 数据源")
    dataset = load_dataset(root)
    instance = dataset.instance(dataset.default_instance_id)

    print(f"游戏目录：{game}")
    print(f"数据实例：{instance.path_id}")

    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("需要 Pillow 才能把图落盘：pip install Pillow") from None

    selected = [f for f in FAMILIES if args.only is None or f.name in args.only]
    if not selected:
        raise SystemExit(f"--only 没匹配到任何族。可选：{[f.name for f in FAMILIES]}")

    report: dict[str, dict] = {}
    for family in selected:
        keys = _keys_of(instance, family)
        if not keys:
            print(f"[{family.name}] 没有键，跳过")
            continue
        started = time.perf_counter()
        directory = args.out / family.name
        directory.mkdir(parents=True, exist_ok=True)

        files: dict[str, str] = {}
        missing: list[str] = []
        bundles: set[str] = set()
        used: set[str] = set()
        for key in keys:
            payload = resolver.resolve(family.name, key)
            if payload is None:
                missing.append(key)
                continue
            where = resolver.describe(key)
            if where is not None:
                bundles.add(where[1])
            stem = _sanitize(key)
            if stem in used:
                stem = f"{stem}_{hashlib.sha1(key.encode('utf-8')).hexdigest()[:6]}"
            used.add(stem)
            try:
                name = _save_image(Image.open(io.BytesIO(payload)), directory / stem)
            except Exception as exc:  # 单张失败不该毁掉整族
                missing.append(f"{key}  (解码失败: {exc})")
                continue
            files[key] = f"{family.name}/{name}"

        stats = {
            "label": family.label,
            "source": family.column,
            "bundles": sorted(bundles),
            "keys": len(keys),
            "found": len(files),
            "missing": missing,
            "files": files,
            "seconds": round(time.perf_counter() - started, 1),
        }
        report[family.name] = stats
        print(
            f"[{family.name}] {family.label} · 命中 {stats['found']}/{stats['keys']}"
            f"（{stats['seconds']}s）"
        )
        if missing:
            print(f"  缺失 {len(missing)} 个：{missing[:5]}")

    # ❗ **合并**而不是覆盖。
    # 实踩：跑 --only unit_portraits 时直接覆盖 manifest，把其余六族全丢了，
    # 界面于是整片退回剪影。--only 是"只重跑这几族"，不是"只要这几族"。
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / "_manifest.json"
    merged: dict[str, dict] = {}
    if manifest_path.is_file():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            merged = dict(previous.get("families") or {})
        except (OSError, ValueError):
            merged = {}
    merged.update(report)

    manifest = {
        "game_dir": str(game),
        "instance": instance.path_id,
        "families": merged,
        "total_keys": sum(int(v.get("keys", 0)) for v in merged.values()),
        "total_found": sum(int(v.get("found", 0)) for v in merged.values()),
        "this_run": sorted(report),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\n本次 {sum(v['found'] for v in report.values())} 张"
        f" · 缓存合计 {manifest['total_found']}/{manifest['total_keys']}"
        f"（{len(merged)} 族） -> {args.out}"
    )
    return 0 if report else 1


if __name__ == "__main__":
    sys.exit(main())
