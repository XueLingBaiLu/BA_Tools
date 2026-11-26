r"""**模型档案**：把一台车的完整资产信息一次性拿出来，并可选落盘成 markdown。

## 为什么有它（2026-10-10 用户点名）

用户原话：「**你们似乎从来没有把模型的完整信息记住**」

事实：`DLC2_CV9035` 这台车，今晚被**反复重新挖**了至少五次 ——
「哪个渲染器用哪颗材质」「shader 是哪支」「`Layer_A97CDC25_1` 是什么」
「它有几条皮肤」「它在哪个包」「军械库看不看得见」「贴图的 alpha 会不会把车身抠掉」…
每一次都是**现场再量一遍**，量完就丢了。

⇒ 这个工具把上面那些**一次全打出来**，并且能用 `--write` 落成
`docs/模型档案/<地址键>.md` —— 那才是「记住」：**可复算的生成物，不是聊天记录**。

## 一条命令

    .venv\Scripts\python.exe tools\model_card.py --hudname CV-9035 --write
    .venv\Scripts\python.exe tools\model_card.py --unit DLC2_CV9035
    .venv\Scripts\python.exe tools\model_card.py --selftest    # 选根规则夹具
                                                              # ★负控：多候选无匹配必须报错

## 它会回答

1. **数据库那一行**：Id / Name / HUDName / ModelFileName / Cost / **DisplayInArmory** /
   **IsUnitModification**（后两列 = 用户在军械库里够不够得着它）
2. **地址 → 候选包 → 命中包**（Addressables：一个键可能对上多个包）
3. **prefab 根**：pathID、GameObject 名、**Skins.Data 条数**（有皮肤 = 材质会被 SetMaterials 覆盖）
   ＋ **选根规则** —— ⚠ 一个 prefab 里可能有**多台**单位（`…/DLC2/DLC2_CV9035.prefab` 里塞着
   DLC2 全套 **25 台**载具），所以选根按「**精确名 → 唯一候选 → 子串唯一**」；
   **都匹配不上就报错**，⛔ **不许静默取第一个** —— 2026-10-10 就是取了第一个，
   把 `DLC2_AS365_Dauphin`（海豚直升机）写成了 CV-9035 的档案。判据 `--selftest`。
4. **渲染器 → 材质**（含**子网格数** —— 材质位下标 >= 子网格数的那一位 Unity 会忽略）
5. **材质 → shader + 每个贴图槽指向谁**
6. **每张被引用的贴图**：尺寸 / 格式 / 平均 RGB / **平均 alpha** / 透明像素占比
   —— ⚠ **alpha 才是不透明度的真变量**（2026-10-10 踩过：法线贴图 A=125 ⇒ 车身被抠空）
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _s.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_GAME = Path(r"D:\Steam\steamapps\common\broken_arrow")
DB_COLS = ("Id", "Name", "HUDName", "ModelFileName", "Cost",
           "DisplayInArmory", "IsUnitModification", "Type", "CountryId")


def _pid(p) -> int:
    return int((p or {}).get("m_PathID", 0) or 0) if isinstance(p, dict) else 0


def _plist(v):
    if isinstance(v, dict):
        d = v.get("Data")
        return d if isinstance(d, list) else []
    return v if isinstance(v, list) else []


def _db_rows(zip_path: Path):
    with zipfile.ZipFile(zip_path) as zf:
        doc = json.loads(zf.read("Units.json").decode("utf-8"))
    return doc["Rows"] if isinstance(doc, dict) and "Rows" in doc else doc


def pick_row(rows, *, row=None, unit=None, hudname=None, uid=None):
    """挑数据库那一行 —— ⚠ **HUDName 不唯一**（全库 38 组重名），

    所以命中多于一条时**不许静默挑第一条**，把候选全列出来让调用者自己定
    （口径见 AGENTS §0.13；2026-10-10 就是因为静默挑了重名里的第一条，
    把一台**军械库看不见**的车当成了测试目标，白跑两次启动）。
    """
    if row is not None:
        return [(int(row), rows[int(row)])]
    hits = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        same_id = uid is not None and str(r.get("Id")) == str(uid)
        same_unit = unit and str(r.get("ModelFileName") or "").lower() == unit.lower()
        same_hud = hudname and str(r.get("HUDName") or "").lower() == hudname.lower()
        if same_id or same_unit or same_hud:
            hits.append((i, r))
    return hits


class RootPickError(RuntimeError):
    """选不出「这个地址键对应的那个 UnitPrefabRoot」。"""


#: 选根规则名（会印进档案 §3，让读数自己说明它是怎么被挑出来的）
ROOT_RULE_EXACT = "精确：GameObject 名 == 地址键"
ROOT_RULE_ONLY = "唯一候选"
ROOT_RULE_SUBSTR = "子串唯一命中"


def pick_unit_root(cands: list[tuple[int, str]], key: str) -> tuple[tuple[int, str], str]:
    """从 [(pathID, GameObject 名)] 里选**该地址键对应的那一个** UnitPrefabRoot。

    判据（2026-10-10 定。起因：`Assets/Resources_moved/ModelPrefabs/DLC2/DLC2_CV9035.prefab`
    里塞着 **DLC2 全套 25 台载具**，而老实现取**扫描顺序的第一个** ⇒ 生成的《模型档案 ·
    DLC2_CV9035》整份描述的是 `DLC2_AS365_Dauphin`（海豚直升机）。）

      ① **精确**（不分大小写）`GameObject 名 == key` ⇒ 用它；
      ② 只有**一个**候选 ⇒ 用它（自造包 / 单台包走这条）；
      ③ 否则**子串唯一**匹配（互相包含）⇒ 唯一命中才用；
      ④ 都不成立 ⇒ **抛 `RootPickError`** —— ⛔ 不许静默取第一个：
         本文件上面处理 HUDName 重名时已经写死过同一条规矩「不许静默挑第一条」，
         选根这里当时没做到，就是这次翻车的形状。
    """
    k = (key or "").strip().lower()
    if not cands:
        raise RootPickError("这个 prefab 里一个 UnitPrefabRoot 都没有")
    if k:
        exact = [c for c in cands if (c[1] or "").strip().lower() == k]
        if len(exact) == 1:
            return exact[0], ROOT_RULE_EXACT
        if len(exact) > 1:
            raise RootPickError(f"{len(exact)} 个候选的名字都等于 {key!r}：{[c[1] for c in exact]}")
    if len(cands) == 1:
        return cands[0], ROOT_RULE_ONLY
    if k:
        sub = [c for c in cands if k in (c[1] or "").lower() or (c[1] or "").lower() in k]
        if len(sub) == 1:
            return sub[0], ROOT_RULE_SUBSTR
    raise RootPickError(
        f"{len(cands)} 个候选里没有与 {key!r} 匹配的 UnitPrefabRoot："
        + ", ".join(repr(c[1]) for c in cands[:8]))


def build(game: Path, row_idx: int, row: dict) -> dict:
    from PIL import Image

    from ba_mod_maker.asset_kernel.unitypy_source import open_unique_source
    from ba_mod_maker.core.assets.catalog import AddressablesCatalog
    from ba_mod_maker.core.assets.game import bundle_dir, catalog_file

    info: dict = {"db": {c: row.get(c) for c in DB_COLS}, "row_index": row_idx}
    key = str(row.get("ModelFileName") or "")
    info["address_key"] = key
    bd, cp = bundle_dir(game), catalog_file(game)
    cat = AddressablesCatalog.load(cp.parent)
    hits = cat.lookups(key) if key else []
    assets = [a for a, _b in hits if a and a.endswith(".prefab")]
    info["candidate_bundles"] = sorted({b for _a, b in hits if b})
    info["asset_path"] = assets[0] if assets else None
    info.update({"renderers": [], "materials": [], "textures": [], "skins_count": None})
    if not assets:
        info["error"] = f"catalog 里没有 {key!r} 的 .prefab 地址"
        return info

    cands = tuple(info["candidate_bundles"])
    with open_unique_source(bd, cands, info["asset_path"]) as (src, bname):
        info["hit_bundle"] = bname
        cand_roots: list[tuple[int, str]] = []
        for pid in src.object_ids("MonoBehaviour"):
            try:
                t = src.read_typetree(pid)
            except Exception:
                continue
            if not {"PrefabRoot", "Renderers", "Skins"} <= set(t.keys()):
                continue
            nm = ""
            gp = _pid(t.get("m_GameObject"))
            if gp:
                with contextlib.suppress(Exception):
                    nm = str(src.read_typetree(gp).get("m_Name") or "")
            cand_roots.append((pid, nm))
        info["root_candidates"] = [nm for _p, nm in cand_roots]
        try:
            (root_pid, root_go), info["root_pick_rule"] = pick_unit_root(cand_roots, key)
        except RootPickError as exc:
            info["error"] = f"选不出 prefab 根：{exc}"
            return info

        matname: dict[int, str] = {}
        for p in src.object_ids("Material"):
            with contextlib.suppress(Exception):
                matname[p] = str(src.read_typetree(p).get("m_Name") or "")

        root = src.read_typetree(root_pid)
        sk = root.get("Skins") or {}
        d = sk.get("Data") if isinstance(sk, dict) else None
        info["root_path_id"] = root_pid
        info["root_game_object"] = root_go
        info["skins_count"] = len(d) if isinstance(d, list) else -1
        for pp in _plist(root.get("Renderers")):
            r = _pid(pp)
            try:
                rt = src.read_typetree(r)
            except Exception:
                info["renderers"].append({"path_id": r, "error": "读不到"})
                continue
            ms = [_pid(m) for m in (rt.get("m_Materials") or [])]
            mp = _pid(rt.get("m_Mesh"))
            nsub = None
            if mp:
                try:
                    nsub = len(src.read_typetree(mp).get("m_SubMeshes") or [])
                except Exception:
                    nsub = None
            info["renderers"].append({
                "path_id": r, "enabled": rt.get("m_Enabled"), "submeshes": nsub,
                "materials": [matname.get(m, f"<{m}>") for m in ms],
                "ignored_slots": [i for i in range(len(ms))
                                  if isinstance(nsub, int) and nsub > 0 and i >= nsub],
            })

        used = sorted({m for rr in info["renderers"] for m in rr.get("materials", [])})
        tex_pids: set[int] = set()
        for p, nm in matname.items():
            if nm not in used:
                continue
            tt = src.read_typetree(p)
            shp = _pid(tt.get("m_Shader"))
            shn = ""
            if shp:
                try:
                    st = src.read_typetree(shp)
                    pf = st.get("m_ParsedForm") or {}
                    shn = str(pf.get("m_Name") or st.get("m_Name") or "")
                except Exception:
                    shn = f"<{shp}>"
            slots = {}
            for e in (tt.get("m_SavedProperties") or {}).get("m_TexEnvs") or ():
                k = e[0] if isinstance(e, (list, tuple)) else None
                v = e[1] if isinstance(e, (list, tuple)) and len(e) > 1 else {}
                tp = _pid((v or {}).get("m_Texture")) if isinstance(v, dict) else 0
                if not tp:
                    continue
                try:
                    slots[k] = str(src.read_typetree(tp).get("m_Name") or "")
                except Exception:
                    slots[k] = f"<{tp}>"
                tex_pids.add(tp)
            info["materials"].append({"name": nm, "shader": shn, "slots": slots})

        for tp in src.object_ids("Texture2D"):
            if tp not in tex_pids:
                continue
            try:
                t = src.read_typetree(tp)
            except Exception:
                continue
            w, h = int(t.get("m_Width") or 0), int(t.get("m_Height") or 0)
            rec = {"name": str(t.get("m_Name") or ""), "width": w, "height": h,
                   "format": t.get("m_TextureFormat")}
            try:
                im = Image.frombytes("RGBA", (w, h), src.decode_texture_rgba(tp))
                d = list(im.resize((32, 32)).get_flattened_data())
                n = len(d)
                rec["mean_rgb"] = [round(sum(q[i] for q in d) / n) for i in range(3)]
                rec["mean_alpha"] = round(sum(q[3] for q in d) / n, 1)
                rec["transparent_pct"] = round(sum(1 for q in d if q[3] < 128) / n * 100, 1)
            except Exception as e:
                rec["decode_error"] = type(e).__name__
            info["textures"].append(rec)
    return info


def to_markdown(info: dict) -> str:
    lines = [f"# 模型档案 · {info['db'].get('HUDName')}（{info.get('address_key')}）", "",
         "> 生成物，别手改；重跑：`.venv\\Scripts\\python.exe tools\\model_card.py "
         f"--unit {info.get('address_key')} --write`", "", "## 1. 数据库那一行", "",
         "| 列 | 值 |", "|---|---|"]
    lines += [f"| `{k}` | {v!r} |" for k, v in info["db"].items()]
    vis = bool(info["db"].get("DisplayInArmory")) and not info["db"].get("IsUnitModification")
    lines += ["", f"**军械库可见 = {vis}**"
              "（判据：`DisplayInArmory is True` 且 `IsUnitModification` 不是 True）",
          "", "## 2. 地址与包", "",
          f"* 地址键：`{info.get('address_key')}` → `{info.get('asset_path')}`",
          f"* 候选包 {len(info.get('candidate_bundles') or [])} 个 · "
          f"命中：`{info.get('hit_bundle')}`",
          "", "## 3. prefab 根", "",
          f"* pathID `{info.get('root_path_id')}` · GameObject {info.get('root_game_object')!r}",
          "* 选根：**" + str(info.get("root_pick_rule") or "—") + "**"
          + f"（这个 prefab 里有 {len(info.get('root_candidates') or [])} 个 UnitPrefabRoot"
          + (": " + "、".join(f"`{n}`" for n in (info.get("root_candidates") or [])[:8])
             + ("…" if len(info.get("root_candidates") or []) > 8 else "") + "）"
             if (info.get("root_candidates") or []) else "）"),
          f"* **`Skins.Data` = {info.get('skins_count')}**"
          + ("（**有皮肤 ⇒ 运行期 `Renderer.SetMaterials` 会覆盖材质**）"
             if (info.get("skins_count") or 0) > 0 else "（无皮肤 ⇒ prefab 上的材质就是最终外观）"),
          "", "## 4. 渲染器 → 材质", "",
          "| 渲染器 pathID | 启用 | 子网格 | 材质 | 会被忽略的材质位 |", "|---|---|---|---|---|"]
    for r in info["renderers"]:
        lines.append(f"| `{r.get('path_id')}` | {r.get('enabled')} | {r.get('submeshes')} | "
                     f"{r.get('materials')} | {r.get('ignored_slots')} |")
    lines += ["", "## 5. 材质 → shader → 槽", ""]
    for m in info["materials"]:
        lines += [f"### `{m['name']}` · shader `{m['shader']}`", ""]
        pairs = [f"* `{k}` → `{v}`" for k, v in sorted(m["slots"].items())]
        lines += pairs or ["* （无贴图槽）"]
        lines.append("")
    lines += ["## 6. 被引用的贴图（⚠ **alpha 才是不透明度的真变量**）", "",
          "| 贴图 | 尺寸 | fmt | 平均 RGB | 平均 A | 透明占比 |", "|---|---|---|---|---|---|"]
    for t in sorted(info["textures"], key=lambda x: -(x.get("mean_alpha") or 0)):
        lines.append(f"| `{t['name']}` | {t.get('width')}x{t.get('height')} | "
                      f"{t.get('format')} | {t.get('mean_rgb')} | {t.get('mean_alpha')} | "
                      f"{t.get('transparent_pct')}% |")
    return "\n".join(lines) + "\n"


def selftest() -> int:
    """选根规则的夹具 —— ★负控最重要：**多候选且都不匹配时必须报错**，
    ⛔ 不许静默取第一个（老实现就是这么把海豚直升机写成 CV-9035 的）。"""
    pa, pb, pc = 1001, 1002, 1003          # 三个假 pathID，只用于夹具
    ok = bad = 0

    def check(tag: str, cands, key: str, want_pid=None, want_raise: bool = False) -> None:
        nonlocal ok, bad
        try:
            (pid, _nm), rule = pick_unit_root(cands, key)
        except RootPickError as exc:
            if want_raise:
                ok += 1
                print(f"  ✔ {tag}：按预期报错（{exc}）")
            else:
                bad += 1
                print(f"  ✘ {tag}：不该报错，却报错（{exc}）")
            return
        if want_raise:
            bad += 1
            print(f"  ✘ {tag}：**该报错却静默选了** pid={pid}（{rule}）")
        elif pid == want_pid:
            ok += 1
            print(f"  ✔ {tag}：选中 pid={pid}（{rule}）")
        else:
            bad += 1
            print(f"  ✘ {tag}：选错了 pid={pid}（期望 {want_pid}）")

    many = [(pa, "DLC2_AS365_Dauphin"), (pb, "DLC2_CV9035"), (pc, "DLC2_HMMWV")]
    print("选根规则夹具：")
    check("正控·精确名", many, "DLC2_CV9035", want_pid=pb)
    check("正控·大小写不敏感", many, "dlc2_cv9035", want_pid=pb)
    check("负控·唯一候选（自造包）", [(pa, "BALT_CR9037")], "CR9037P3Mat01", want_pid=pa)
    check("负控·子串唯一", [(pa, "DLC2_AS365_Dauphin"), (pb, "DLC2_CV9035_extra")],
          "DLC2_CV9035", want_pid=pb)
    check("★负控·多候选无匹配 ⇒ 必须抛", many, "DLC2_Unknown", want_raise=True)
    check("★负控·空候选 ⇒ 必须抛", [], "DLC2_CV9035", want_raise=True)
    print(f"结论：{ok} 条命中 / {bad} 条失败")
    return 0 if bad == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="一台车的完整资产档案")
    ap.add_argument("--game", default=str(DEFAULT_GAME))
    ap.add_argument("--source", help="BA_Mod.zip（默认 <game> 下的）")
    ap.add_argument("--row", type=int)
    ap.add_argument("--unit", help="ModelFileName / 地址键")
    ap.add_argument("--hudname")
    ap.add_argument("--id", dest="uid")
    ap.add_argument("--write", action="store_true", help="写成 docs/模型档案/<地址键>.md")
    ap.add_argument("--selftest", action="store_true",
                    help="跑选根规则的夹具（★负控：多候选无匹配必须报错，不许静默取第一个）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()

    z = Path(a.source) if a.source else Path(a.game) / "BA_Mod.zip"
    if not z.is_file():
        print(f"找不到 {z}")
        return 2
    rows = _db_rows(z)
    hits = pick_row(rows, row=a.row, unit=a.unit, hudname=a.hudname, uid=a.uid)
    if not hits:
        print("没匹配到单位（--row / --unit / --hudname / --id 四选一）")
        return 1
    if len(hits) > 1:
        print(f"⚠ 「{a.hudname or a.unit or a.uid}」命中了 {len(hits)} 条 —— "
              "**HUDName 会重名，不许静默挑第一条**，请用 --id 或 --row 指定：")
        for i, r in hits:
            print(f"   行{i:<4} Id={r.get('Id'):<6} HUDName={r.get('HUDName')!r:<22} "
                  f"Name={r.get('Name')!r:<24} Model={r.get('ModelFileName')!r} "
                  f"DisplayInArmory={r.get('DisplayInArmory')!r}")
        return 1
    idx, row = hits[0]
    info = build(Path(a.game), idx, row)
    if a.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    md = to_markdown(info)
    print(md)
    if a.write:
        d = Path(__file__).resolve().parents[1] / "docs" / "模型档案"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{info.get('address_key')}.md"
        p.write_text(md, encoding="utf-8")
        print(f"\n已落盘 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
