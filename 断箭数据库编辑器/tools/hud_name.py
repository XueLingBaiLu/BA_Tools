r"""单位标识换算：Id / 行下标 / Name / ModelFileName  <->  **HUDName**。

## 为什么有这个小工具（2026-10-10 用户点名）

用户原话：「**我记不住ID，你说 HUDName**」「**对用户也一律用HUDName吧**」。

依据（kb/20 跳 2 · kb/18 §4.1）：**游戏里显示的名字 = Units.HUDName ＋ 缺省选项
Options.ConcatenateWithUnitName** —— 卡名是**拼出来的**，不是本地化串；
Units.Id / Units.Name / 行下标都是**我们内部的**东西，用户看不到。

⇒ 所以凡是**跟用户说话**、或**写用户要看的文档**，一律用 HUDName；
本工具负责把两边对上（也负责在不得不写 Id 时，把 HUDName 一起带上）。

判据一行：
    .venv\Scripts\python.exe tools\hud_name.py CV-9035
      -> **CV-9035**  （行下标=405）  Id=443  Name='BALT CV-9035'
                         ModelFileName='CR9037P3Mat01'  Cost=70
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import zipfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _s.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_GAME = Path(r"D:\Steam\steamapps\common\broken_arrow")
COLS = ("Id", "Name", "HUDName", "ModelFileName", "Cost")


def _rows(zip_path: Path):
    with zipfile.ZipFile(zip_path) as zf:
        raw = zf.read("Units.json").decode("utf-8")
    doc = json.loads(raw)
    return doc["Rows"] if isinstance(doc, dict) and "Rows" in doc else doc


def find(rows, needle: str):
    n, low = needle.strip(), needle.strip().lower()
    out = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        if n in (str(r.get("Id")), str(i)) or low in (
            str(r.get("Name") or "").lower(),
            str(r.get("HUDName") or "").lower(),
            str(r.get("ModelFileName") or "").lower(),
        ):
            out.append((i, r))
    if not out:
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                continue
            hud = str(r.get("HUDName") or "").lower()
            nm = str(r.get("Name") or "").lower()
            if low in hud or low in nm:
                out.append((i, r))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把 Id / 行下标 / 名字 换成 HUDName（反之亦然）")
    ap.add_argument("needle", nargs="?", help="HUDName / Name / Id / 行下标 / ModelFileName")
    ap.add_argument("--game", default=str(DEFAULT_GAME))
    ap.add_argument(
        "--source",
        help="直接给 BA_Mod.zip（默认 <game>\\BA_Mod\\BA_Mod.zip —— 2026-10-27 起的位置）",
    )
    ap.add_argument("--all", action="store_true", help="列出全部（只为确认口径，很长）")
    a = ap.parse_args(argv)

    # ★ 2026-10-27：外部库搬进 `<游戏根>\BA_Mod\`（与资产包同一个文件夹）。
    #   唯一口径在 core/session.py::PACKAGE_DIR / PACKAGE_REL_PATH，这里按同一串拼。
    z = Path(a.source) if a.source else Path(a.game) / "BA_Mod" / "BA_Mod.zip"
    if not z.is_file():
        print(f"找不到 {z}（可用 --source 指定）")
        return 2
    rows = _rows(z)
    if a.all:
        print(f"库 = {z}   共 {len(rows)} 行")
        for i, r in enumerate(rows):
            print(f"  {r.get('HUDName')!r:<28} Id={r.get('Id')}  行={i}  Name={r.get('Name')!r}")
        return 0
    if not a.needle:
        ap.print_help()
        return 2
    hits = find(rows, a.needle)
    if not hits:
        print(f"没有匹配「{a.needle}」（判据：命中 0 条）  —— 库 = {z} / {len(rows)} 行")
        return 1
    for i, r in hits:
        extra = "  ".join(f"{c}={r.get(c)!r}" for c in COLS if c != "HUDName")
        print(f"  **{r.get('HUDName')}**  （行下标={i}）  {extra}")
    print("\n=> 对用户就说 **HUDName**（上面加粗那个）；写 Id / 行下标时必须同时带上它。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
