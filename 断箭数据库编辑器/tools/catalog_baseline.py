r"""catalog 基线读数一把抓（换游戏版本后重记用）。

游戏每次更新，`tests/test_catalog_bin.py` / `tests/test_catalog_bin_write.py` /
`tests/test_cli_address_inventory.py` 里那几组**出厂读数**就要跟着重记。以前那条重记命令是
一条 500 字符的 `python -c`（抄不动、还过不了 ruff 的 100 列），所以收到这里来：

    .venv\Scripts\python.exe tools\catalog_baseline.py

输出是**逐行 `名字 = 值`**，与测试里的常量一一对应（下面「输出」那张表就是口径）。
⛔ 只读游戏目录，一个字节都不写。

## 输出（2026-10-18 实测，1.2.1 出厂件）

    catalog = catalog.bin
    catalog_bytes = 2755558
    catalog_sha256 = 6adccfb6a2b580c7…
    catalog_hash_file = 1b56bf58…
    hash128_matches_file = True
    counts = 27764 27764 20614
    bundles = 76
    units_bundle = units_assets_all_43d88558….bundle
    units_size = 3352484707
    units_crc = 265969015
    units_hash = 43d88558…

JSON 那一代（≤1.2.0）会把前四行换成 json 的读数、并给出 `keys/buckets/entries` 三个数。
"""

from __future__ import annotations

import hashlib
import sys

from ba_mod_maker.core.assets import catalog_bin as cbin
from ba_mod_maker.core.assets import catalog_bin_write as cbw
from ba_mod_maker.core.assets.game import catalog_file, catalog_hash_file, find_game_root


def _print(name: str, value: object) -> None:
    print(f"{name} = {value}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    root = find_game_root()
    if root is None:
        print("找不到游戏目录（BA_MOD_MAKER_GAME 可覆盖）", file=sys.stderr)
        return 2
    path = catalog_file(root)
    if not path.is_file():
        print(f"盘上没有 catalog：{path}", file=sys.stderr)
        return 2
    raw = path.read_bytes()
    _print("game", root)
    _print("catalog", path.name)
    _print("catalog_bytes", len(raw))
    _print("catalog_sha256", hashlib.sha256(raw).hexdigest())
    if not cbw.looks_like_catalog_bin(raw):
        # ≤1.2.0：JSON 那份。计数头用产品自己的解析（与 catalog_edit 同一把尺子）。
        from ba_mod_maker.asset_kernel import catalog_edit as ce

        edit = ce.CatalogEdit.from_bytes(raw)
        _print("keys", edit.counts[0])
        _print("buckets", edit.counts[1])
        _print("entries", edit.counts[2])
        _print("internal_ids", len(edit.internal_ids))
        names = [
            str(edit.to_table().key_value(i))
            for i in range(len(edit.keys))
            if str(edit.to_table().key_value(i)).startswith("units_assets_all")
        ]
        _print("units_bundle", ", ".join(sorted(set(names))))
        return 0
    counts = cbw.counts(raw)
    _print("counts", " ".join(str(n) for n in counts))
    entries = cbin.bundle_entries(path)
    _print("bundles", len(entries))
    hash_file = catalog_hash_file(root)
    if hash_file.is_file():
        _print("catalog_hash_file", hash_file.read_text(encoding="utf-8").strip())
        _print("hash128_matches_file", cbw.hash128_hex(raw) == hash_file.read_text(
            encoding="utf-8").strip())
    units = next((n, e) for n, e in entries.items() if n.startswith("units_assets_all"))
    _print("units_bundle", units[0])
    _print("units_size", units[1].bundle_size)
    _print("units_crc", units[1].crc)
    _print("units_hash", units[1].hash_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
