r"""W3 只读导入的机械判据（工单 §3 的 D1~D5）。

**判据一行**：导入 RU_T72B -> **13 renderer / 3 材质 / 3 贴图**，位置误差 **< 1e-4 m**，
贴图**逐像素 diff = 0**。

| 判据 | 通过线 | 本单实测 |
|---|---|---|
| D1 数量 | 13 / 3 / 3 | 真建出 13 个 renderer 对象（12 网格 + 1 占位）|
| D1 附 | LOD 分解 | 4 级 / LOD0 = 7 / 并集 = 13（LOD1 与 LOD0 共用 3 个） |
| D2 位置 | < 1e-4 m | **1.669e-07 m**（52 个节点全量；两条独立路径） |
| D3 绕序 | 占比 = 1.0000 | 12 网格 80,185 三角 **0.998566**；9/12 恰好 1.000000 |
| D3 负控 | 必须能红 | 倒绕序 0.068、换回乙式 0.066（ammo 那边 0.000441） |
| D4 贴图 | diff = 0 | 3/3 **diff_pixels = 0**，colorspace sRGB/sRGB/Non-Color |
| D5 节点名 | 5 个契约名 | 层级路径 **52/52** 相同；**BaseArmor 本 prefab 没有** |
| D6 骨架 | 1 / 30 / 29 / 12 | 骨 30 根（29 可形变）、12 件挂修改器；rest 位移 **~1e-7 m** |

三条纪律（AGENTS §0.9 动作 2 / 设计稿 §11）：

1. **每条判据自带负控**：喂一个已知违规的点进去，读数必须变红；
2. **尺子只用产品自己的标识**（模块::函数 / 容器属性 / 组件名）；
3. **计数类判据扫全量**（没有 --limit 这种只给显示用的参数）。

## 两条实测更正（工单 §3 写的数与资产不符，这里是现场读数）

1. **D3 的「1.0000」只在 9/12 个网格上成立**。全 12 网格是 **0.998566**（80,185 个三角里
   115 个不同意），而且这 115 个**全部**落在 Chassis / 02_Chassis / 03_Chassis 三个网格上
   （面法线的 |dot| ~1e-4 量级 = 小三角 + 被 soften 的顶点法线）。
   ammoprefabs 那 86 个网格（把 .resS 也解出来之后）同样量过：122,352 个三角
   frac = 0.999534，57 个不同意，集中在 9 个名字带 **soften_normals** 的导弹网格上
   （soften_normals9 一个就占 16 个）。**这不是绕序翻了**（翻了会掉到 0.0），
   是源数据里那批小三角的顶点法线本来就和面法线不同向。
   ⇒ 判据取「frac >= 0.998 **且**不同意的三角全部落在那 3 个网格里」，
   并且**照样带负控**（倒绕序 / 换回乙式都必须能翻到 0.07 以下）。
2. **D5 的 BaseArmor 不在 RU_T72B_Base.prefab 里**。实测 52 个节点的名字里没有它
   （反编译侧 UnitBuilder.txt:26587 是 Assets.FindChildren(Chassis, "BaseArmor")，
   找不到会走 null 分支，**不是硬要求**）。所以判据改成：
   **52 个节点的层级路径与源 52/52 相同**（改名 = 静默不生效的那条硬约束），
   并把 4 个确实存在的契约名逐个点名。

## 为什么用真实资产

D1~D5 的数就是这辆车的数（设计稿 §3.2）。小夹具验不了「13/3/3」也验不了
「.resS 里那 87 万字节是不是基色图」。

## 内存账（2026-10-03 实测，**别再退回旧形状**）

打开 units 包（3.38 GB）**一个进程就要 10.3 GB**（实测 pytest 一个 worker 的
WorkingSet 峰值 **10,294 MB**）。第一版把这件事放在 pytest 进程里做，而 -n auto 下
本文件有 6 条用例要它 ⇒ 6 × 10.3 ≈ 60 GB，机器只有 31 GB ⇒ **MemoryError**，
用户当场强制停止。

现在的形状（三条一起才成立，改动时别拆）：

1. **3.38 GB 的包只在一个短命子进程里开**（python tests/test_import_unit.py
   --emit-units-reading）：它把读数写成普通数据落盘就退出 —— 进程一退，那 10 GB 立刻还给系统；
2. pytest 侧只读那份 JSON（而且是用**纯 Python** 拿归一化节点现场重建 prefab 树，
   所以树 / 世界坐标这些判据仍然在测试进程里真跑）；
3. 源码侧读数与 Blender 探针**共用一把重活锁**（_work/w3_heavy.lock）：
   同一时刻只允许一个重活进程。

改完实测：**19 passed / 44 s / 全部 python+blender 合计峰值 10,792 MB**。
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import (
    coords,
    mesh,
    prefab,
    skeleton,
    streaming,
    unitypy_source,
)
from ba_mod_maker.asset_kernel.colorspace import colorspace_of

# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
#: 工作区根：_work/ 与 DataBaseCompiled/ 都在这一层（仓库是它的子目录）。
WORKSPACE = REPO_ROOT.parent
REFS_DIR = WORK / "blender-design" / "assets" / "textures"
PROBE = REPO_ROOT / "blender_addon" / "import_probe.py"
DEFAULT_BLENDER = Path(r"D:\Blender\blender.exe")

UNITS_PREFIX = "units_assets_all"
AMMO_PREFIX = "ammoprefabs"
UNIT_KEY = "RU_T72B"
ASSET_PATH = "Assets/Resources_moved/ModelPrefabs/RU/RU_T72B_Base.prefab"

#: 设计稿 §11 A2 的锚点（W2 的 C1 判据）：7 个内联网格 / 6622 个三角。
W2_INLINE_MESHES = 7
W2_INLINE_TRIANGLES = 6622

#: 补上 .resS 之后的读数（本单实测）。
AMMO_MESHES = 86
AMMO_TRIANGLES = 122352
AMMO_VERTICES = 139568

#: T-72B 底盘（设计稿 §3.2 的 13 / 3 / 3）。
T72B_NODES = 52
T72B_RENDERERS = 13
T72B_MATERIALS = 3
T72B_TEXTURES = 3
T72B_LOD_LEVELS = 4
T72B_LOD0 = 7
T72B_LOD_UNION = 13

#: 绕序锚点（本单实测，两种口径都给）。
T72B_WINDING = {"checked": 80185, "positive": 80070, "negative": 115, "degenerate": 34}
T72B_LOD0_WINDING = {"checked": 63176, "positive": 63098, "negative": 78, "degenerate": 24}
#: ⚠ 这三个数是 **W2 的 coords.winding_agreement** 量出来的（首顶点法线 + |cross| < 1e-14
#: 记退化）。用「三个顶点法线取平均」那种写法会得到 122,352 / 122,295 / 57 —— 同一批
#: 数据、不同尺子，别混。判据一律走 W2 那一条。
AMMO_WINDING = {"checked": 122349, "positive": 122264, "negative": 85, "degenerate": 3}

#: 3 张贴图的参考件（_work/blender-design/assets/textures/，export_textures.py 生成）。
T72B_TEXTURE_REFS: tuple[tuple[str, int, int, str], ...] = (
    ("RU_T72B_1_BaseMap", 2048, 2048, "T72B__RU_T72B_1_BaseMap.png"),
    (
        "RU_Track7290_Standard_22_AlbedoTransparency",
        512,
        512,
        "T72B__RU_Track7290_Standard_22_AlbedoTransparency.png",
    ),
    (
        "RU_Track7290_Standard_22_Normal",
        512,
        512,
        "T72B__RU_Track7290_Standard_22_Normal.png",
    ),
)

#: 参考 PNG 的 sha256（RGBA 原始字节）。⛔ 这三个数改了说明参考件换了 —— 先复核再改锚点。
REFERENCE_SHA256: dict[str, str] = {
    "T72B__RU_T72B_1_BaseMap.png": (
        "93404319ae77024cb4f3bb5eb4a58a1ce61d0b8d97d0af446f50f875051e2a8d"
    ),
    "T72B__RU_Track7290_Standard_22_AlbedoTransparency.png": (
        "ccd5b68cea97c9f314c98aba64a73f3907809cfa10bd7f3dcc626933a686688d"
    ),
    "T72B__RU_Track7290_Standard_22_Normal.png": (
        "73a570146d159104434857d34cdde9bd39240f5d3306d36375021c0e1cc7071f"
    ),
}


# --------------------------------------------------------------------------- #
# 现场与共用件
# --------------------------------------------------------------------------- #
def _bundle_dir() -> Path | None:
    from ba_mod_maker.core.assets.game import bundle_dir, find_game_root

    root = find_game_root()
    return bundle_dir(root) if root is not None else None


def _require_bundle(prefix: str) -> Path:
    base = _bundle_dir()
    hits = sorted(base.glob(f"{prefix}*.bundle")) if base is not None else []
    if not hits:
        pytest.skip(f"找不到 {prefix}*.bundle（没装游戏 / BA_MOD_MAKER_GAME 指错了）")
    return hits[0]


def _game_root() -> Path:
    from ba_mod_maker.core.assets.game import find_game_root

    root = find_game_root()
    if root is None:
        pytest.skip("找不到游戏根目录")
    return Path(root)


def _blender_path() -> Path | None:
    override = os.environ.get("BA_BLENDER")
    if override:
        path = Path(override)
        return path if path.is_file() else None
    return DEFAULT_BLENDER if DEFAULT_BLENDER.is_file() else None


@functools.lru_cache(maxsize=1)
def _ammo() -> tuple[unitypy_source.AssetSource, tuple[dict, ...]]:
    """ammoprefabs 包的读源 + 全部 Mesh 的 typetree（86 个）。"""
    source = unitypy_source.open_source(_require_bundle(AMMO_PREFIX))
    trees = tuple(
        obj.read_typetree() for obj in source.env.objects if obj.type.name == "Mesh"
    )
    return source, trees


# --------------------------------------------------------------------------- #
# units 包（3.38 GB）：**一个短命子进程**算完就退出
# --------------------------------------------------------------------------- #
#: 为什么不在 pytest 进程里开这个包：实测**一个** worker 打开它就峰值 **10,294 MB**
#: （Get-Process python 采到的 WorkingSet 峰值）。-n auto 下本文件有 6 条用例要它，
#: 同时开就是 ~60 GB —— 31 GB 的机器直接 MemoryError（2026-10-03 真在用户机器上出现过，
#: 用户当场强制停止）。所以重活放进**子进程**，输出 JSON 落盘，测试只读 JSON：
#: ① 峰值 = 一个进程；② 进程一退，那 3.38 GB 立刻还给系统（不指望解释器归还）。
UNITS_JSON = WORK / "w3_units_reading.json"
#: 重活锁：源码侧读数与 Blender 探针**共用**一把 —— 同一时刻只允许一个重活进程。
HEAVY_LOCK = WORK / "w3_heavy.lock"


class _HeavyLock:
    """跨进程互斥（文件锁）：重活一个一个来，别叠。

    锁**陈旧 30 分钟自动失效**：上一次被 Ctrl-C / 强制停止时留下的锁不该卡死这一轮。
    """

    def __init__(self, path: Path, timeout: float = 2400.0) -> None:
        self.path = path
        self.timeout = timeout
        self.fd: int | None = None

    def __enter__(self) -> _HeavyLock:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, f"{os.getpid()}\n".encode())
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    continue
                if age > 1800:
                    self.path.unlink(missing_ok=True)
                    continue
                time.sleep(1.0)
        raise TimeoutError(f"等重活锁超时：{self.path}")

    def __exit__(self, *exc: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.path.unlink(missing_ok=True)


def _source_fingerprint(*extra: str) -> str:
    """指纹 = 额外键 + 被测源码的 mtime；任何一个改了，缓存就作废重跑。"""
    paths = [
        Path(__file__).resolve(),
        PROBE,
        REPO_ROOT / "blender_addon" / "importer.py",
    ]
    paths += sorted((REPO_ROOT / "src" / "ba_mod_maker" / "asset_kernel").glob("*.py"))
    parts = list(extra) + [f"{p.name}:{p.stat().st_mtime_ns}" for p in paths if p.is_file()]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _read_json(path: Path, fingerprint: str) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if data.get("_fingerprint") == fingerprint else None


@functools.lru_cache(maxsize=1)
def _units_reading() -> dict:
    """units 包（T-72B + AH-1Z）的源码侧读数：子进程产出 + 落盘缓存 + 跨 worker 共享。"""
    fingerprint = _source_fingerprint("units", UNIT_KEY, AH1Z_KEY)
    cached = _read_json(UNITS_JSON, fingerprint)
    if cached is not None:
        return cached
    with _HeavyLock(HEAVY_LOCK):
        cached = _read_json(UNITS_JSON, fingerprint)
        if cached is not None:
            return cached
        reading = _run_units_reader()
        reading["_fingerprint"] = fingerprint
        UNITS_JSON.write_text(json.dumps(reading, ensure_ascii=False), encoding="utf-8")
        return reading


def _run_units_reader() -> dict:
    """跑一个短命子进程去开那个 3.38 GB 的包（--emit-units-reading）。"""
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--emit-units-reading"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert proc.returncode == 0, (
        f"units 读数子进程退出码 {proc.returncode}\n{(proc.stdout or '')[-2000:]}"
        f"\n{(proc.stderr or '')[-2000:]}"
    )
    match = re.search(r"^UNITS_BEGIN$(.*?)^UNITS_END$", proc.stdout, re.M | re.S)
    assert match, f"子进程没打印 JSON 块\n{(proc.stdout or '')[-2000:]}"
    reading, _end = json.JSONDecoder().raw_decode(match.group(1).lstrip())
    return reading


def _emit_units_reading() -> int:
    """python tests/test_import_unit.py --emit-units-reading：真算一遍，打 JSON 就退。"""
    reading = {"units": _collect_units()}
    print("UNITS_BEGIN")
    print(json.dumps(reading, ensure_ascii=False, indent=1))
    print("UNITS_END")
    sys.stdout.flush()
    return 0

def _json_materials(raw: dict) -> dict[int, dict]:
    """JSON 把 dict 的 int 键变成了字符串，转回来（材质表按 pathID 索引）。"""
    return {int(key): value for key, value in raw.items()}


def _unit_reading(key: str) -> dict:
    return _units_reading()["units"][key]


def _t72b_tree() -> prefab.PrefabTree:
    """T-72B 的树：**在 pytest 进程里**用落盘的归一化节点现场重建（纯 Python，不碰 UnityPy）。"""
    unit = _unit_reading(UNIT_KEY)
    return prefab.build_tree(unit["nodes"], _json_materials(unit["materials"]))


def _ah1z_tree() -> prefab.PrefabTree:
    unit = _units_reading()["units"]["ah1z"]
    return prefab.build_tree(unit["nodes"], _json_materials(unit["materials"]))


# --------------------------------------------------------------------------- #
# 子进程里干的重活：把 units 包的读数抓成**普通数据**
# --------------------------------------------------------------------------- #
def _wg(report: object) -> dict:
    """WindingReport -> 普通 dict（子进程与测试之间只传数据）。"""
    return {
        "checked": report.checked,  # type: ignore[attr-defined]
        "positive": report.positive,  # type: ignore[attr-defined]
        "negative": report.negative,  # type: ignore[attr-defined]
        "degenerate": report.degenerate,  # type: ignore[attr-defined]
        "out_of_range": report.out_of_range,  # type: ignore[attr-defined]
        "frac": report.frac,  # type: ignore[attr-defined]
    }


def _prefab_numbers(tree: prefab.PrefabTree) -> dict:
    """一棵 prefab 树的**纯数字**读数（不含任何对象引用）。"""
    lod0 = set(tree.lod_members(0))
    lod0_renderers = [r for r in tree.renderers if r.component_id in lod0]
    return {
        "nodes": len(tree.nodes),
        "renderers": len(tree.renderers),
        "materials": len(tree.materials),
        "textures": len(tree.texture_ids()),
        "smr": sum(1 for r in tree.renderers if r.kind == prefab.SKINNED_MESH_RENDERER),
        "mr": sum(1 for r in tree.renderers if r.kind == prefab.MESH_RENDERER),
        "material_names": sorted(tree.material_name(m) for m in tree.materials),
        "lod_levels": len(tree.lods),
        "lod_size": tree.lod_size,
        "lod0": len(lod0),
        "lod_union": len(tree.lod_union()),
        "lod_outside": len(tree.renderers_outside_lods()),
        "lod0_names": [tree.renderer_of(c).name for c in tree.lod_members(0)],  # type: ignore[union-attr]
        "lod1_names": [tree.renderer_of(c).name for c in tree.lod_members(1)],  # type: ignore[union-attr]
        "lod0_materials": len({m for r in lod0_renderers for m in r.material_ids}),
        "names": [n.name for n in tree.nodes],
        "paths": [n.path for n in tree.nodes],
    }


def _collect_units() -> dict:
    """**真开一次** units 包（3.38 GB），把 D1~D5 要的源码侧读数全抓成数据。"""
    from ba_mod_maker.core.assets.catalog import AddressablesCatalog

    aa_dir = _require_bundle(UNITS_PREFIX).parents[1]
    pc_dir = aa_dir / "PC"
    catalog = AddressablesCatalog.load(aa_dir)
    out: dict[str, object] = {
        "catalog_keys": catalog.key_count if catalog is not None else 0,
        "bundle_bytes": _require_bundle(UNITS_PREFIX).stat().st_size,
    }

    hits = catalog.lookups(UNIT_KEY)  # type: ignore[union-attr]
    candidates = tuple(sorted({bundle for _asset, bundle in hits if bundle}))
    source, bundle = unitypy_source.open_unique_bundle(pc_dir, candidates, ASSET_PATH)
    try:
        nodes, materials = source.read_prefab_nodes(ASSET_PATH)
        tree = prefab.build_tree(nodes, materials)
        info = _prefab_numbers(tree)
        info["key"] = UNIT_KEY
        info["asset_path"] = ASSET_PATH
        info["bundle"] = bundle
        info["candidates"] = list(candidates)
        # 归一化节点 / 材质一起落盘：worker 侧能用 **纯 Python** 现场重建这棵树
        # （判据里的树逻辑因此仍然在 pytest 进程里真跑，而不是读一个预先算好的数）。
        info["nodes"] = nodes
        info["materials"] = {str(key): value for key, value in materials.items()}
        lod0 = set(tree.lod_members(0))

        # ---- 负控：砍掉一个 SkinnedMeshRenderer 再建树，renderer 数必须跟着掉 ----
        trimmed = [dict(node) for node in nodes]
        for node in trimmed:
            comps = tuple(
                c for c in node.get("components") or () if c.get("kind") != "SkinnedMeshRenderer"
            )
            if len(comps) != len(node.get("components") or ()):
                node["components"] = comps
                break
        info["trimmed_renderers"] = len(prefab.build_tree(trimmed, materials).renderers)

        # ---- 网格 + 绕序（含两条负控与逐网格的不一致账）----
        mesh_of: dict[int, mesh.MeshData] = {}
        for renderer in tree.renderers:
            if renderer.mesh_id and not renderer.mesh_file:
                mesh_of[renderer.component_id] = source.read_mesh(renderer.mesh_id)
        ordered = [r for r in tree.renderers if r.component_id in mesh_of]
        meshes = [mesh_of[r.component_id] for r in ordered]
        info["meshes"] = [
            [
                r.name,
                mesh_of[r.component_id].name,
                mesh_of[r.component_id].vertex_count,
                mesh_of[r.component_id].triangle_count,
            ]
            for r in ordered
        ]
        info["mesh_total_triangles"] = sum(m.triangle_count for m in meshes)
        info["winding"] = _wg(coords.winding_agreement(meshes))
        info["winding_reversed"] = _wg(coords.winding_agreement(meshes, reverse=True))
        # 负控：换回 2026-10-03 之前的"乙式"（det=+1 的纯旋转）。read_mesh 出来已经补过绕序，
        # 所以此时必须整体翻过去 —— 这条就是"为什么契约非得是反射"的读数。
        yi = lambda v: (v[0], -v[2], v[1])  # noqa: E731  -- 乙式就一行，负控专用
        yi_matrix = tuple(tuple(yi(b)[r] for b in coords._BASIS) for r in range(3))
        info["mirror_det"] = coords.determinant3(yi_matrix)
        info["winding_mirror"] = _wg(coords.winding_agreement(meshes, transform=yi))
        lod0_meshes = [mesh_of[r.component_id] for r in ordered if r.component_id in lod0]
        info["lod0_mesh_count"] = len(lod0_meshes)
        info["winding_lod0"] = _wg(coords.winding_agreement(lod0_meshes))
        offenders = []
        for renderer in ordered:
            single = coords.winding_agreement([mesh_of[renderer.component_id]])
            if single.negative or single.degenerate:
                offenders.append(
                    [
                        renderer.name,
                        mesh_of[renderer.component_id].name,
                        single.negative,
                        single.degenerate,
                    ]
                )
        info["offenders"] = offenders

        # ---- 世界坐标锚点 + 错矩阵负控 ----
        body = tree.nodes_named("body")[0]
        lr000 = tree.nodes_named("LR000")[0]
        info["world"] = {
            "body": list(tree.world_position(body.transform_id)),
            "body_local": list(body.position),
            "lr000": list(tree.world_position(lr000.transform_id)),
            "lr000_local": list(lr000.position),
            "checked_nodes": len(tree.nodes),
        }
        real = prefab.trs_matrix
        prefab.trs_matrix = _row_vector_trs  # type: ignore[assignment]
        try:
            broken = prefab.build_tree(nodes, materials)
            info["row_matrix_worst"] = max(
                max(
                    abs(a - b)
                    for a, b in zip(
                        broken.world_position(n.transform_id),
                        tree.world_position(n.transform_id),
                        strict=True,
                    )
                )
                for n in tree.nodes
            )
        finally:
            prefab.trs_matrix = real  # type: ignore[assignment]

        # ---- 骨骼名哈希（设计稿 §8 规则 4 / §13 R12）----
        chassis = next(r for r in tree.renderers if r.name == "Chassis")
        mesh_tt = source.read_typetree(chassis.mesh_id)
        hashes = skeleton.read_bone_hashes(mesh_tt)
        check = skeleton.verify_bone_hashes(tree.bone_paths(chassis.bone_ids), hashes)
        info["bones"] = {
            "slots": len(chassis.bone_ids),
            "hashes": len(hashes),
            "matched": len(check.matched),
            "unmatched": len(check.unmatched),
            "empty": check.empty_slots,
            "root_hash": skeleton.root_bone_hash(mesh_tt),
            "crc_root_body": skeleton.bone_path_hash("root", "body"),
            "crc_body": skeleton.bone_path_hash("body"),
            "body_path": body.path,
            "lr001_path": tree.nodes_named("LR001")[0].path,
        }

        # ---- 贴图：.resS -> 字节 -> 解码 -> 与参考 PNG 逐像素比 ----
        rows = []
        decoded: dict[str, bytes] = {}
        for tex_id in tree.texture_ids():
            tinfo = source.read_texture(tex_id)
            rgba = source.decode_texture_rgba(tex_id)
            decoded[tinfo.name] = rgba
            row = {
                "id": tex_id,
                "name": tinfo.name,
                "width": tinfo.width,
                "height": tinfo.height,
                "format": tinfo.format,
                "color_space": tinfo.color_space,
                "colorspace_name": colorspace_of(tinfo.color_space),
                "image_size": tinfo.image_size,
                "raw_len": len(tinfo.raw),
                "raw_sha256": hashlib.sha256(tinfo.raw).hexdigest(),
                "decoded_sha256": hashlib.sha256(rgba).hexdigest(),
                "hash_mismatch": tinfo.hash_mismatch,
                "streamed": tinfo.streamed,
            }
            reference = next(
                (ref for name, _w, _h, ref in T72B_TEXTURE_REFS if name == tinfo.name), None
            )
            if reference is not None:
                ref_bytes = _reference_rgba(reference)
                row["reference"] = reference
                row["reference_sha256"] = hashlib.sha256(ref_bytes).hexdigest()
                row["diff_pixels"] = (
                    None
                    if len(ref_bytes) != len(rgba)
                    else sum(
                        1
                        for index in range(0, len(rgba), 4)
                        if rgba[index : index + 4] != ref_bytes[index : index + 4]
                    )
                )
            rows.append(row)
        info["textures"] = rows
        info["reference_sha256_anchors"] = dict(REFERENCE_SHA256)

        # 负控 A：法线的参考件去比基色图 -> 必须不同
        normal_ref = _reference_rgba("T72B__RU_Track7290_Standard_22_Normal.png")
        info["wrong_reference_differs"] = (
            decoded.get("RU_Track7290_Standard_22_AlbedoTransparency") != normal_ref
        )

        # 负控 B：DXT5Crunched 的解包函数由 version 选 —— 传 (0,0,0,0) 会解出错的图
        from UnityPy.export.Texture2DConverter import parse_image_data

        base_row = next(r for r in rows if r["name"] == "RU_T72B_1_BaseMap")
        obj = source.object_at(base_row["id"])
        data = obj.read()
        reader = getattr(data, "object_reader", None) or obj
        base_info = source.read_texture(base_row["id"])
        platform_blob = source.read_typetree(base_row["id"]).get("m_PlatformBlob")
        good = parse_image_data(
            base_info.raw,
            base_info.width,
            base_info.height,
            base_info.format,
            reader.version,
            reader.platform,
            platform_blob,
            True,
        )
        wrong = parse_image_data(
            base_info.raw,
            base_info.width,
            base_info.height,
            base_info.format,
            (0, 0, 0, 0),
            reader.platform,
            None,
            True,
        )
        info["crunched"] = {
            "format": base_info.format,
            "good_sha256": hashlib.sha256(good.convert("RGBA").tobytes()).hexdigest(),
            "wrong_sha256": hashlib.sha256(wrong.convert("RGBA").tobytes()).hexdigest(),
        }

        # ---- AH-1Z（回归：不在任何 LOD 级里的 renderer 不许被当 LOD 变体）----
        ah_nodes, ah_materials = source.read_prefab_nodes(AH1Z_ASSET)
        ah = prefab.build_tree(ah_nodes, ah_materials)
        out["ah1z"] = _prefab_numbers(ah)
        out["ah1z"]["key"] = AH1Z_KEY
        out["ah1z"]["asset_path"] = AH1Z_ASSET
        out["ah1z"]["nodes"] = ah_nodes
        out["ah1z"]["materials"] = {str(key): value for key, value in ah_materials.items()}

        out["RU_T72B"] = info
    finally:
        source.close()
    return out



#: 探针报告的落点：跑一次 blender，把 JSON 落盘给同一个 pytest 会话里的**所有** worker 用。
#: 为什么必须落盘：-n auto 会把 7 个用报告的用例分到不同 worker，每个 worker 各起一个
#: Blender 进程 + 各开一次 3.38 GB 的包 —— 实测直接 MemoryError。
#: 按**单位**分开存：一个会话里可能验不止一辆车（本单还有一个 AH-1Z 的回归）。
def _report_paths(unit: str) -> tuple[Path, Path]:
    slug = re.sub(r"[^0-9A-Za-z_-]+", "_", unit)
    json_path = WORK / f"w3_import_report_{slug}.json"
    return json_path, json_path.with_suffix(".lock")


def _fingerprint(unit: str) -> str:
    """指纹 = 单位 + 探针 / 插件 / 内核的 mtime；任何一个改了，缓存就作废重跑。"""
    return _source_fingerprint("probe", unit)


def _import_report(unit: str = UNIT_KEY) -> dict:
    """跑一次 blender -b 的无头导入探针，拿现场读数（跨 worker 共享，落盘缓存）。"""
    report_json, _unused_lock = _report_paths(unit)
    fingerprint = _fingerprint(unit)
    cached = _read_report(report_json, fingerprint)
    if cached is not None:
        return cached
    # 与源码侧读数**共用一把重活锁**：同一时刻只允许一个重活进程，免得
    # 「Blender 的那份 3.38 GB 视图」与「pytest 的那份」叠在一起。
    with _HeavyLock(HEAVY_LOCK):
        cached = _read_report(report_json, fingerprint)
        if cached is not None:
            return cached
        report = _run_probe(unit)
        report["_fingerprint"] = fingerprint
        report_json.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        return report


def _read_report(report_json: Path, fingerprint: str) -> dict | None:
    if not report_json.is_file():
        return None
    try:
        data = json.loads(report_json.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if data.get("_fingerprint") != fingerprint or not data.get("import_ok"):
        return None
    return data


def _run_probe(unit: str = UNIT_KEY) -> dict:
    blender = _blender_path()
    if blender is None:
        pytest.skip("找不到 blender.exe（设 BA_BLENDER 指过去）")
    if not PROBE.is_file():
        pytest.skip(f"探针不在：{PROBE}")
    env = dict(os.environ)
    slug = re.sub(r"[^0-9A-Za-z_-]+", "_", unit)
    render = WORK / f"w3_{slug}.png"
    proc = subprocess.run(
        [
            str(blender),
            "-b",
            "--factory-startup",
            "--python-exit-code",
            "1",
            "--python",
            str(PROBE),
            "--",
            "--game-root",
            str(_game_root()),
            "--unit",
            unit,
            "--render",
            str(render),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        cwd=str(REPO_ROOT),
        check=False,
        env=env,
    )
    assert proc.returncode == 0, (
        f"Blender 退出码 {proc.returncode}\n{(proc.stdout or '')[-4000:]}"
    )
    match = re.search(
        r"^BAMOD_IMPORT_BEGIN$(.*?)^BAMOD_IMPORT_END$", proc.stdout, re.M | re.S
    )
    assert match, f"探针没打印 JSON 块\n{(proc.stdout or '')[-4000:]}"
    # ⚠ 不能直接 json.loads：Blender 退出时会往 stdout 再写一行版本号，缓冲顺序
    #    决定了它可能夹在 JSON 与 END 之间（实测就是这样）。用 raw_decode 只吃
    #    第一个完整的 JSON 值，后面的垃圾不管。
    report, _end = json.JSONDecoder().raw_decode(match.group(1).lstrip())
    assert report.get("import_ok"), f"导入失败：{report.get('import_error')}"
    return report


def _reference_rgba(file_name: str) -> bytes:
    from PIL import Image

    with Image.open(REFS_DIR / file_name) as handle:
        return handle.convert("RGBA").tobytes()


# --------------------------------------------------------------------------- #
# D0 · .resS 流式定位（D1~D5 都靠它）
# --------------------------------------------------------------------------- #
class _FakeNode:
    def __init__(self, path: str, offset: int, size: int) -> None:
        self.path = path
        self.offset = offset
        self.size = size


class _FakeBundle:
    """最小容器：节点表 + 一段合成解压流（够 resolve_stream 用）。"""

    def __init__(self, nodes: list[_FakeNode], stream: bytes) -> None:
        self.nodes = nodes
        self._stream = stream

    def read_range(self, offset: int, size: int) -> bytes:
        if offset < 0 or offset + size > len(self._stream):
            raise ValueError("越界")
        return self._stream[offset : offset + size]


def test_resolve_stream_locates_the_node_and_honours_the_offset() -> None:
    """正控：basename 精确命中 + **节点内相对偏移**；四条负控全部能红。"""
    stream = bytes(range(256)) * 4
    bundle = _FakeBundle(
        [_FakeNode("CAB-aaaa", 0, 256), _FakeNode("CAB-aaaa.resS", 256, 768)], stream
    )
    info = streaming.StreamingInfo("archive:/CAB-zzzz/CAB-aaaa.resS", 10, 6)
    payload = streaming.resolve_stream(bundle, info)
    assert payload.data == stream[256 + 10 : 256 + 16]
    assert payload.matched_by == "basename" and payload.hash_mismatch is False
    assert payload.node_offset == 256

    # 负控 1：区间越界必须抛（节点名对了、偏移错了）
    with pytest.raises(streaming.StreamedDataError):
        streaming.resolve_stream(
            bundle, streaming.StreamingInfo("archive:/x/CAB-aaaa.resS", 760, 32)
        )
    # 负控 2：内联对象（path 空）不许当流式
    with pytest.raises(streaming.StreamedDataError):
        streaming.resolve_stream(bundle, streaming.StreamingInfo("", 1, 2))
    # 负控 3：哈希对不上 + 两个流式节点 -> **不许猜**
    two = _FakeBundle(
        [_FakeNode("CAB-a.resS", 0, 100), _FakeNode("CAB-b.resS", 100, 100)], stream
    )
    with pytest.raises(streaming.StreamedDataError):
        streaming.resolve_stream(two, streaming.StreamingInfo("archive:/x/CAB-old.resS", 4, 8))
    # 正控 2：哈希对不上但流式节点唯一 -> 读，且**留痕**
    one = _FakeBundle([_FakeNode("CAB-new.resS", 128, 128)], stream)
    old = streaming.StreamingInfo("archive:/x/CAB-old.resS", 4, 8)
    fallback = streaming.resolve_stream(one, old)
    assert fallback.hash_mismatch is True and fallback.matched_by == "single-stream-node"
    # 负控 4：传错对象（认不出 path/offset/size）必须抛，不许静默当内联
    with pytest.raises(streaming.StreamedDataError):
        streaming.resolve_stream(bundle, {"m_Name": "not a streaming info"})


def test_vertex_stream_layout_matches_every_real_mesh_byte_for_byte() -> None:
    """顶点流布局：dimension & 0xF + 流间 16 字节对齐 —— 86/86 个网格逐字节相等。

    负控：**不掩 dimension**（拿 52 当维数）时，79 个流式网格的长度立刻对不上。
    """
    source, trees = _ammo()
    resolver = source.stream_resolver()
    exact = 0
    raw_mismatch = 0
    for tree in trees:
        vertex_data = tree["m_VertexData"]
        channels = list(vertex_data["m_Channels"])
        count = int(vertex_data["m_VertexCount"])
        data = mesh.as_bytes(vertex_data.get("m_DataSize"))
        if not data:
            info = mesh.stream_info_of(tree)
            assert info is not None and info.is_streamed
            data = resolver(info)
        assert mesh.layout_span(channels, count) == len(data), tree.get("m_Name")
        exact += 1
        # 负控：**不掩 dimension**（直接拿 52 当维数）算出来的步长必须与掩过的不一样
        strides = mesh.stream_layout(channels, count)[0]
        naive: dict[int, int] = {}
        for channel in channels:
            dim = int(channel.get("dimension", 0) or 0)
            if dim <= 0:
                continue
            stream = int(channel.get("stream", 0) or 0)
            stride = int(channel.get("offset", 0) or 0) + dim * mesh.FORMAT_SIZE[
                int(channel.get("format", 0) or 0)
            ]
            naive[stream] = max(naive.get(stream, 0), stride)
        if naive != strides:
            raw_mismatch += 1
    assert exact == AMMO_MESHES, f"逐字节相等的网格数应当是 {AMMO_MESHES}，实测 {exact}"
    assert raw_mismatch == 79, (
        "负控应当有 79 个网格的步长被 dimension=52 算错，实测 "
        f"{raw_mismatch} —— 掩码若失效，这条尺子就没咬合"
    )


def test_streamed_meshes_decode_and_agree_with_unitypy_and_the_aabb() -> None:
    """补上 .resS：86/86 可解；顶点/法线与 UnityPy 逐点相同；位置范围 == m_LocalAABB。"""
    source, trees = _ammo()
    ids = [obj.path_id for obj in source.env.objects if obj.type.name == "Mesh"]
    scan = mesh.scan_meshes(trees, path_ids=ids, stream=source.stream_resolver())
    assert (len(scan.meshes), scan.triangles, scan.vertices) == (
        AMMO_MESHES,
        AMMO_TRIANGLES,
        AMMO_VERTICES,
    ), f"读数与锚点不符：meshes={len(scan.meshes)} triangles={scan.triangles}"
    assert scan.streamed == [] and scan.stream_failures == [] and scan.malformed == []

    # 与 UnityPy 自己的解码逐顶点对拍（不同实现、同一份字节）
    from UnityPy.helpers.MeshHelper import MeshHandler

    checked = 0
    for decoded in scan.meshes:
        obj = next(
            o for o in source.env.objects if o.type.name == "Mesh" and o.path_id == decoded.path_id
        )
        handler = MeshHandler(obj.read())
        handler.process()
        assert handler.m_Vertices is not None and handler.m_Normals is not None
        assert len(handler.m_Vertices) == decoded.vertex_count
        for i in range(decoded.vertex_count):
            assert tuple(handler.m_Vertices[i]) == decoded.positions[i], (decoded.name, i)
            assert tuple(handler.m_Normals[i][:3]) == decoded.normals[i], (decoded.name, i)
        checked += decoded.vertex_count
    assert checked == AMMO_VERTICES

    # 独立的自证：解出来的位置范围必须与资产**自己声明的** m_LocalAABB 相符
    bad = []
    for tree, decoded in zip(trees, scan.meshes, strict=True):
        aabb = tree["m_LocalAABB"]
        center = (aabb["m_Center"]["x"], aabb["m_Center"]["y"], aabb["m_Center"]["z"])
        extent = (aabb["m_Extent"]["x"], aabb["m_Extent"]["y"], aabb["m_Extent"]["z"])
        for axis in range(3):
            low = min(p[axis] for p in decoded.positions)
            high = max(p[axis] for p in decoded.positions)
            if (
                abs(low - (center[axis] - extent[axis])) > 1e-3
                or abs(high - (center[axis] + extent[axis])) > 1e-3
            ):
                bad.append(decoded.name)
                break
    assert bad == [], f"这些网格的位置范围与 m_LocalAABB 不符：{bad[:5]}"


# --------------------------------------------------------------------------- #
# D1 · 数量（13 / 3 / 3）
# --------------------------------------------------------------------------- #
def test_d1_renderer_material_texture_counts_and_lod_split() -> None:
    """prefab 树里数出 13/3/3，并把四级 LOD 的分解钉住（工单 §7 坑 6）。"""
    tree = _t72b_tree()
    assert len(tree.nodes) == T72B_NODES
    assert len(tree.renderers) == T72B_RENDERERS
    assert len(tree.materials) == T72B_MATERIALS
    assert len(tree.texture_ids()) == T72B_TEXTURES

    kinds = [r.kind for r in tree.renderers]
    assert kinds.count(prefab.SKINNED_MESH_RENDERER) == 12
    assert kinds.count(prefab.MESH_RENDERER) == 1
    assert sorted(tree.material_name(m) for m in tree.materials) == [
        "LODs",
        "RU_T72B 1",
        "RU_T72B_track",
    ]

    # LOD：4 级、LOD0 = 7、四级并集 = 13（LOD1 与 LOD0 共用 T72BA / K1 / K5）
    assert (len(tree.lods), tree.lod_size) == (T72B_LOD_LEVELS, 5.0)
    assert len(tree.lod_members(0)) == T72B_LOD0
    assert len(tree.lod_union()) == T72B_LOD_UNION
    assert tree.renderers_outside_lods() == ()
    assert [tree.renderer_of(c).name for c in tree.lod_members(0)] == [  # type: ignore[union-attr]
        "Chassis",
        "RU_TanktrakL",
        "RU_TanktrakR",
        "BaseMobility",
        "T72BA",
        "K1",
        "K5",
    ]
    assert [tree.renderer_of(c).name for c in tree.lod_members(1)] == [  # type: ignore[union-attr]
        "000_skinned_Chassis",
        "001_skinned_RU_TanktrakL",
        "002_skinned_RU_TanktrakR",
        "003_skinned_BaseMobility",
        "T72BA",
        "K1",
        "K5",
    ]
    assert tree.lod_members(4) == ()  # 索引越界给空，不给错的一级

    # only_lod0 那一档的读数（7 个 renderer / 2 个材质 / 3 张贴图）—— 也钉住
    lod0 = set(tree.lod_members(0))
    lod0_renderers = [r for r in tree.renderers if r.component_id in lod0]
    assert len(lod0_renderers) == T72B_LOD0
    assert len({m for r in lod0_renderers for m in r.material_ids}) == 2

    # 负控：少一个 renderer，计数必须跟着掉（尺子不是常数）
    unit = _unit_reading(UNIT_KEY)
    nodes, materials = unit["nodes"], _json_materials(unit["materials"])
    trimmed = [dict(n) for n in nodes]
    for node in trimmed:
        comps = tuple(
            c for c in node.get("components") or () if c.get("kind") != "SkinnedMeshRenderer"
        )
        if len(comps) != len(node.get("components") or ()):
            node["components"] = comps
            break
    assert len(prefab.build_tree(trimmed, materials).renderers) == T72B_RENDERERS - 1


def test_d1_blender_really_created_13_renderers_and_3_textures() -> None:
    """在 Blender 里**重新数一遍**（不是抄 report）：13 个 renderer 对象 / 3 材质 / 3 张图。"""
    report = _import_report()
    assert report["counts"]["nodes"] == T72B_NODES
    assert report["counts"]["renderers"] == T72B_RENDERERS
    assert report["counts"]["materials"] == T72B_MATERIALS
    assert report["counts"]["textures"] == T72B_TEXTURES
    assert report["counts"]["mesh_objects"] == 12
    assert report["counts"]["placeholder_objects"] == 1
    assert report["counts"]["lod_levels"] == T72B_LOD_LEVELS
    assert report["counts"]["lod0_members"] == T72B_LOD0
    assert report["counts"]["lod_union"] == T72B_LOD_UNION
    assert report["counts"]["lod_hidden"] == T72B_LOD_UNION - T72B_LOD0
    # 场景里的对象数 = 52 个节点（工厂启动另有 3 个默认对象：Cube/Camera/Light）
    assert report["mesh_detail"], "网格明细是空的 —— 判据会空跑"
    assert len(report["mesh_detail"]) == 12, "12 个有网格的 renderer 各一条明细"
    assert sorted(report["material_names"]) == [
        "RU_T72B__LODs",
        "RU_T72B__RU_T72B 1",
        "RU_T72B__RU_T72B_track",
    ]
    # 12 个有网格的对象都有 UV 与 38 个骨骼槽的顶点组
    for item in report["mesh_detail"]:
        assert item["uv_layers"] == ["UVMap"], item
        assert item["vgroups"] == 38, item
    # 占位（外部引用）必须**显式记账**
    assert any("lod_1x1x1" in note and "m_FileID=2" in note for note in report["unresolved"])


# --------------------------------------------------------------------------- #
# D2 · 位置（< 1e-4 m）
# --------------------------------------------------------------------------- #
#: 先抓住**真**的那一份：monkeypatch 之后 _row_vector_trs 里还要用（否则自递归）。
_REAL_TRS_MATRIX = prefab.trs_matrix


def _row_vector_trs(position, rotation, scale):
    """负控用的错矩阵：平移写进第 4 **行**（本单第一版就是这个错）。"""
    right = _REAL_TRS_MATRIX(position, rotation, scale)
    return (
        right[0], right[1], right[2], 0.0,
        right[4], right[5], right[6], 0.0,
        right[8], right[9], right[10], 0.0,
        right[3], right[7], right[11], 1.0,
    )


def test_d2_unity_world_positions_are_column_vector_trs() -> None:
    """世界坐标的锚点 + 负控：平移写进第 4 行时，误差必须爆掉。"""
    tree = _t72b_tree()
    body = tree.nodes_named("body")[0]

    def world_of(tree_obj: prefab.PrefabTree, name: str) -> tuple[float, float, float]:
        return tree_obj.world_position(tree_obj.nodes_named(name)[0].transform_id)

    # 锚点：body 的直接父链没有平移，所以它的世界坐标就是自己的局部平移
    # （走了一趟 4x4 矩阵乘法，所以留 1e-9 的浮点余量，不写 ==）
    # ⚠ 余量取 1e-6 而不是 1e-9：Unity 的 Transform 是 **float32**，进 Python 是
    #   float64，过一次矩阵乘就会差 ~1.6e-7（实测）。D2 的通过线 1e-4 仍在。
    body_world = world_of(tree, "body")
    assert max(
        abs(a - b) for a, b in zip(body_world, body.position, strict=True)
    ) < 1e-6
    assert abs(body_world[1] - 0.8366947770118713) < 1e-6
    # LR000 挂在 body 下：世界坐标必须**带上**父级的那 0.8367 m ——
    # 这正是「平移写进第 4 行」时丢掉的那一项（丢了就退回自己的局部坐标）。
    lr000 = tree.nodes_named("LR000")[0]
    lr000_world = world_of(tree, "LR000")
    assert abs(lr000_world[1] - lr000.position[1]) > 0.8, (
        "父级平移没被复合进去 —— 世界坐标退化成局部坐标了"
    )
    # 父链上有小旋转，简单相加只是近似（实测差 5.3e-6），所以这里留 1e-4
    expected = tuple(
        a + b for a, b in zip(lr000.position, body.position, strict=True)
    )
    assert max(
        abs(a - b) for a, b in zip(lr000_world, expected, strict=True)
    ) < 1e-4

    # 负控：同一个树，只把 trs_matrix 换成"平移写第 4 行" -> 深层节点的误差必须 > 1e-4
    real = prefab.trs_matrix
    prefab.trs_matrix = _row_vector_trs  # type: ignore[assignment]
    try:
        unit = _unit_reading(UNIT_KEY)
        broken = prefab.build_tree(unit["nodes"], _json_materials(unit["materials"]))
        worst = max(
            max(
                abs(a - b)
                for a, b in zip(
                    broken.world_position(node.transform_id),
                    tree.world_position(node.transform_id),
                    strict=True,
                )
            )
            for node in tree.nodes
        )
    finally:
        prefab.trs_matrix = real  # type: ignore[assignment]
    assert worst > 1e-4, f"负控没咬合：错矩阵的最大偏差只有 {worst}"


def test_d2_blender_world_positions_match_within_1e_4() -> None:
    """Blender 的 matrix_world（独立算的）vs Unity 侧参考：52/52，误差 < 1e-4 m。"""
    report = _import_report()
    position = report["position"]
    assert position["checked"] == T72B_NODES
    assert position["worst_error"] < 1e-4, (
        f"位置误差 {position['worst_error']} m 超了 1e-4（transform "
        f"{position['worst_transform_id']}）"
    )
    # 工单 §3 D2：抽 5 个挂载点逐个比
    assert len(position["samples"]) == 5
    for sample in position["samples"]:
        assert sample["error"] < 1e-4, sample
    assert [s["object"] for s in position["samples"]][:3] == [
        "RU_T72B_Base",
        "death",
        "Chassis",
    ]


# --------------------------------------------------------------------------- #
# D3 · 绕序
# --------------------------------------------------------------------------- #
def test_d3_winding_on_the_real_t72b_meshes_with_negative_controls() -> None:
    """12 个网格的绕序自洽率 + 两条负控（倒绕序 / 换回乙式 det=+1）。

    ⚠ 这些数是**子进程真跑** coords.winding_agreement 算出来的（3.38 GB 的包不在本进程里开，
    见文件头「units 包」那一段的内存账）；负控也真跑了 —— 不是读一个预先写死的布尔。
    """
    reading = _unit_reading(UNIT_KEY)
    assert len(reading["meshes"]) == 12, reading["meshes"]
    assert reading["mesh_total_triangles"] == (
        T72B_WINDING["checked"] + T72B_WINDING["degenerate"]
    )
    report = reading["winding"]
    assert (
        report["checked"],
        report["positive"],
        report["negative"],
        report["degenerate"],
    ) == (
        T72B_WINDING["checked"],
        T72B_WINDING["positive"],
        T72B_WINDING["negative"],
        T72B_WINDING["degenerate"],
    ), f"绕序锚点变了：{report}（游戏更新过？先复核再改锚点）"
    assert report["frac"] >= 0.998, f"frac={report['frac']}"
    assert report["out_of_range"] == 0

    # 逐网格：不同意的三角**全部**落在 Chassis 这三个上（名字有重名，所以按网格名 + mesh 名记）
    offenders = sorted(tuple(row) for row in reading["offenders"])
    assert offenders == sorted(
        [
            ("Chassis", "Chassis", 78, 24),
            ("000_skinned_Chassis", "02_Chassis", 25, 10),
            ("000_skinned_Chassis", "03_Chassis", 12, 0),
        ]
    ), offenders
    assert sum(row[2] for row in offenders) == T72B_WINDING["negative"]

    # LOD0 那 7 个网格单独一档（工单 §7 坑 6 的分解）
    assert reading["lod0_mesh_count"] == T72B_LOD0
    lod0 = reading["winding_lod0"]
    assert (lod0["checked"], lod0["positive"], lod0["negative"]) == (
        T72B_LOD0_WINDING["checked"],
        T72B_LOD0_WINDING["positive"],
        T72B_LOD0_WINDING["negative"],
    )

    # 负控 1：倒绕序
    reversed_report = reading["winding_reversed"]
    assert reversed_report["frac"] < 0.01, (
        f"倒绕序必须把自洽率打到 0.01 以下，实测 {reversed_report['frac']}"
    )
    assert reversed_report["negative"] > T72B_WINDING["checked"] * 0.99

    # 负控 2：换回旧口径乙式（det=+1 的纯旋转）—— 补过绕序的索引配上它必然整体翻过去
    assert reading["mirror_det"] == 1.0, "乙式必须是纯旋转（det=+1）才算负控"
    assert reading["winding_mirror"]["frac"] < 0.01, reading["winding_mirror"]

    # W2 的锚点不许回归：ammoprefabs 那 7 个内联网格仍是 6622/6622
    source, trees = _ammo()
    inline_scan = mesh.scan_meshes(trees)
    assert (len(inline_scan.meshes), inline_scan.triangles) == (
        W2_INLINE_MESHES,
        W2_INLINE_TRIANGLES,
    )
    assert coords.winding_agreement(inline_scan.meshes).frac == 1.0


def test_d3_resolving_res_streams_grows_the_ammo_coverage() -> None:
    """把 .resS 解出来之后覆盖率从 7/86 涨到 86/86；读数如实记账（不掩盖那 57 个）。"""
    source, trees = _ammo()
    scan = mesh.scan_meshes(trees, stream=source.stream_resolver())
    report = coords.winding_agreement(scan.meshes)
    assert (report.checked, report.positive, report.negative) == (
        AMMO_WINDING["checked"],
        AMMO_WINDING["positive"],
        AMMO_WINDING["negative"],
    ), f"ammoprefabs 全量绕序锚点变了：{report}"
    assert report.frac > 0.99
    # 负控：同一批网格倒绕序
    assert coords.winding_agreement(scan.meshes, reverse=True).frac < 0.01


def test_d3_blender_read_back_meshes_keep_the_winding() -> None:
    """读回来的网格（Blender 自己算的 polygon.normal）：绕序没被翻过来。"""
    report = _import_report()
    winding = report["winding"]
    assert winding["checked"] == T72B_WINDING["checked"] + T72B_WINDING["degenerate"]
    assert winding["positive"] / winding["checked"] > 0.99, (
        f"导入后绕序自洽率 {winding['frac']} —— 低于 0.99 说明绕序被翻了"
    )
    assert winding["negative"] < winding["checked"] * 0.002


# --------------------------------------------------------------------------- #
# D4 · 贴图（逐像素 diff = 0 + colorspace）
# --------------------------------------------------------------------------- #
def test_d4_textures_decoded_from_res_stream_match_the_reference_png() -> None:
    """.resS -> 解码 -> 与参考 PNG 逐像素 diff = 0；colorspace 与 m_ColorSpace 逐张一致。

    参考件那一侧是**本进程现场**读的（PIL 打开 PNG 算 sha256），被测那一侧来自子进程的读数
    （同一个 sha256 就等于逐像素 diff = 0，长度一致时两者等价；diff_pixels 是交叉核对）。
    """
    reading = _unit_reading(UNIT_KEY)
    rows = {row["name"]: row for row in reading["textures"]}
    assert set(rows) == {name for name, _w, _h, _ref in T72B_TEXTURE_REFS}

    for name, width, height, reference in T72B_TEXTURE_REFS:
        row = rows[name]
        assert (row["width"], row["height"]) == (width, height)
        assert row["streamed"] is True and row["hash_mismatch"] is False
        assert row["raw_len"] == row["image_size"], f"{name} 的字节数与 m_CompleteImageSize 不符"
        # 参考件本身也要对锚点（换了参考件就该先复核）
        ref_bytes = _reference_rgba(reference)
        ref_sha = hashlib.sha256(ref_bytes).hexdigest()
        assert ref_sha == REFERENCE_SHA256[reference], f"参考 PNG 的 sha256 变了：{reference}"
        assert row["reference"] == reference and row["reference_sha256"] == ref_sha
        assert row["diff_pixels"] == 0, f"{name} 与参考 PNG 有 {row['diff_pixels']} 个像素不同"
        assert row["decoded_sha256"] == ref_sha, f"{name} 解出来的字节与参考件不同"
        # colorspace 按字段判，不按名字判
        assert row["colorspace_name"] == colorspace_of(row["color_space"])
        assert row["colorspace_name"] in {"sRGB", "Non-Color"}
    assert rows["RU_Track7290_Standard_22_Normal"]["colorspace_name"] == "Non-Color"
    assert colorspace_of(rows["RU_T72B_1_BaseMap"]["color_space"]) == "sRGB"

    # 负控 A：拿错参考件必须红（尺子能说不）
    assert reading["wrong_reference_differs"] is True

    # 负控 B：DXT5**Crunched** 的解包函数由 version 选 —— 传 (0,0,0,0) 会静默解出错的图
    crunched = reading["crunched"]
    assert crunched["format"] == 29, "这条负控只对 Crunched 格式有意义"
    anchor = REFERENCE_SHA256["T72B__RU_T72B_1_BaseMap.png"]
    assert crunched["good_sha256"] == anchor, "真 version 解出来的基色图必须与参考件相同"
    assert crunched["wrong_sha256"] != anchor, (
        "version 传错居然还解对了 —— 这条负控失效，别再依赖它"
    )


def test_d4_blender_images_are_pixel_identical_and_carry_the_right_colorspace() -> None:
    """Blender 里那 3 张图：颜色空间对 + 与参考 PNG 逐像素 diff = 0。"""
    report = _import_report()
    images = [item for item in report["images"] if "__" in item["name"]]
    assert len(images) == T72B_TEXTURES, [i["name"] for i in report["images"]]
    by_name = {item["name"].split("__", 1)[-1]: item for item in images}
    expected = {
        "RU_T72B_1_BaseMap": "sRGB",
        "RU_Track7290_Standard_22_AlbedoTransparency": "sRGB",
        "RU_Track7290_Standard_22_Normal": "Non-Color",
    }
    for name, colorspace in expected.items():
        item = by_name[name]
        assert item["colorspace"] == colorspace, item
        assert item["reference"], f"{name} 没找到参考件"
        assert item["pixel_identical"] is True, f"{name} 有 {item['diff_pixels']} 个像素不同"
        assert item["diff_pixels"] == 0
        assert item["nonzero_fraction"] > 0.5, f"{name} 像是空的（图被清了？）"
    # 颜色空间的分布也要对：3 张里 2 张 sRGB、1 张 Non-Color（= 那张法线）
    spread = sorted(item["colorspace"] for item in images)
    assert spread == ["Non-Color", "sRGB", "sRGB"], spread


# --------------------------------------------------------------------------- #
# D5 · 节点名 / 层级路径
# --------------------------------------------------------------------------- #
def test_d5_node_names_paths_and_bone_hashes() -> None:
    """52 个节点一个不少；4 个契约名在；骨骼名哈希 29/29 命中。"""
    tree = _t72b_tree()
    reading = _unit_reading(UNIT_KEY)
    names = [node.name for node in tree.nodes]
    assert len(names) == T72B_NODES
    for wanted in ("root", "body", "Chassis", "BaseMobility"):
        assert wanted in names, f"少了 {wanted}"
    assert "BaseArmor" not in names and "BaseAbility" not in names, (
        "本 prefab 里没有 BaseArmor/BaseAbility —— 反编译侧 UnitBuilder.txt:26587 是"
        " Assets.FindChildren(Chassis, 'BaseArmor')，找不到走 null 分支；"
        "这条断言钉的是「这个 prefab 确实没有」，不是在说判据不必管它"
    )
    # 层级路径（改名 = 静默不生效的那条硬约束按路径判）
    assert tree.nodes_named("body")[0].path.endswith("root/body")
    assert tree.nodes_named("LR001")[0].path.endswith("root/L001/LR001")

    # 骨骼名哈希端到端核对（设计稿 §8 规则 4 / 工单 §13 R12）
    chassis = next(r for r in tree.renderers if r.name == "Chassis")
    assert len(chassis.bone_ids) == 38
    bones = reading["bones"]
    assert (bones["slots"], bones["hashes"]) == (38, 38)
    assert (bones["matched"], bones["unmatched"], bones["empty"]) == (29, 0, 9)
    assert bones["root_hash"] == bones["crc_root_body"]
    assert bones["crc_root_body"] == 0x65A7524A
    assert bones["body_path"].endswith("root/body")
    assert bones["lr001_path"].endswith("root/L001/LR001")
    # 负控：路径少一段就命中不了（这条规则不是"随便都能过"）
    assert skeleton.bone_path_hash("body") != 0x65A7524A
    assert bones["crc_body"] != bones["crc_root_body"]


def test_d5_blender_keeps_every_source_name_and_path() -> None:
    """Blender 侧：52 个名字都在，层级路径 52/52 相同；重名只许加 Blender 的后缀。"""
    report = _import_report()
    assert report["node_name_count"] == T72B_NODES
    assert report["path_mismatch_count"] == 0, report["path_mismatch"]
    assert report["name_presence"] == {
        "root": True,
        "body": True,
        "Chassis": True,
        "BaseArmor": False,
        "BaseMobility": True,
    }
    # Unity 允许同一个 prefab 里重名，Blender 不允许 —— 被改名的必须**显式记账**
    assert report["duplicate_names_in_source"] == ["000_skinned_Chassis"]
    assert report["leaf_renamed"] == [["000_skinned_Chassis", "000_skinned_Chassis.001"]]
    assert report["counts"]["bones_matched"] == 29
    assert report["counts"]["bones_unmatched"] == 0
    assert report["counts"]["bone_empty_slots"] == 9


# --------------------------------------------------------------------------- #
# D6 · 骨架（Armature）：30 根骨 / 12 件挂修改器 / rest 姿势不许挪模型
# --------------------------------------------------------------------------- #
#: 真件读数（设计稿 §3「蒙皮」那一格）：38 个骨槽 -> 30 根骨（29 个真槽 ＋ 把链连起来的 root）。
T72B_BONES = 30
T72B_DEFORM_BONES = 29
#: 挂 Armature 修改器的件数 = 12 个**有网格**的 renderer（第 13 个是占位，没有网格可挂）。
T72B_SKINNED = 12
#: 9 个空槽（炮塔保留位）的名字前缀 —— 槽号保留，但**一根骨都不许建**。
EMPTY_SLOT_PREFIX = "bone_slot_"


def test_d6_blender_builds_the_armature_and_keeps_the_rest_pose() -> None:
    """Blender 现场再数一遍：1 个骨架 / 30 根骨（29 可形变）/ 12 件挂修改器 / rest 位移 ~0。

    ⛔ 骨数、骨名、父链、修改器、rest 位移**全部在 Blender 里重新数**（与 D1 同一条纪律）；
    importer 自己记的那一份（report_* / rig_*）只用来交叉核对 —— 两边对不上就是 bug。

    rest 那条是**补骨架的全部安全性依据**：骨架的 rest 姿势直接取 prefab 节点世界变换，
    所以补完之后模型一个顶点都不许动（armature.rest_max_delta 就是那把尺子）。
    """
    report = _import_report()
    rig = report["armature"]
    assert rig["rig_error"] == "", rig["rig_error"]

    # ① 骨架对象 = 1 个；② 骨 30 根，其中 29 根可形变（root 不形变 —— 它只是把链连起来）
    assert rig["count"] == 1, [item["name"] for item in rig["detail"]]
    assert rig["bone_count"] == T72B_BONES, rig["detail"]
    assert rig["deform_bones"] == T72B_DEFORM_BONES
    armature = rig["detail"][0]
    assert armature["name"] == f"{UNIT_KEY}_Armature"

    # ③ 9 个空槽一根骨都没建（名字是合同：建了反而会与顶点组名对不上）
    placeholders = [n for n in armature["bone_names"] if n.startswith(EMPTY_SLOT_PREFIX)]
    assert placeholders == [], placeholders
    assert len([n for n in armature["bone_names"] if n != "root"]) == T72B_DEFORM_BONES

    # ④ 父链 = prefab 节点链（与 rig_plan 的 C 段同一条，这里是**Blender 侧**再量一遍）
    parents = armature["parents"]
    assert parents["root"] is None
    assert parents["body"] == "root"
    assert parents["LR000"] == "body"
    assert parents["RR007"] == "body"
    assert parents["L003"] == "root"
    assert parents["LR003"] == "L003"

    # ⑤ 12 件蒙皮件各挂一个 Armature 修改器：指向同一个骨架、只认顶点组、不碰骨包络
    assert rig["modifier_name"] == "Armature"
    assert rig["modified_count"] == T72B_SKINNED, [i["name"] for i in rig["modified"]]
    assert sorted(item["name"] for item in rig["modified"]) == sorted(
        item["name"] for item in report["mesh_detail"]
    )
    for item in rig["modified"]:
        assert item["target"] == armature["name"], item
        assert item["use_vertex_groups"] is True, item
        assert item["use_bone_envelopes"] is False, item
        assert item["vgroups"] == 38, item

    # ⑥ rest 恒等（正控）：补骨架**不许**把模型挪位 —— 真件 float32 级（~1e-7），判据 < 1e-4
    assert rig["rest_max_delta_finite"] is True
    assert rig["rest_max_delta"] < 1e-4, f"补骨架把模型挪位了：{rig['rest_max_delta']:.3e} m"
    # 负控（AGENTS §0.9 动作 2：尺子必须先证明**能红**）：探针把 LR003 转 20° 之后，
    # **同一把尺子**必须从 ~1e-7 爆到远大于 1e-4；量完就地复位（否则姿势会带进截图）。
    assert rig["pose_negative_control_error"] == "", rig["pose_negative_control_error"]
    assert rig["pose_negative_control_delta"] > 1e-4, (
        f"摆过姿势这把尺子还是 {rig['pose_negative_control_delta']:.3e} —— 它红不了，不能当判据"
    )
    assert rig["pose_negative_control_delta"] > 100.0 * rig["rest_max_delta"]

    # ⑦ 源侧（importer 自己记的）与 Blender 侧对上
    assert rig["report_armature"] == armature["name"]
    assert rig["report_bones"] == T72B_BONES and rig["report_bones_deform"] == T72B_DEFORM_BONES
    assert sorted(rig["report_armature_modified"]) == sorted(
        item["name"] for item in rig["modified"]
    )
    assert rig["rig_issues"] == [], rig["rig_issues"]
    assert "30 根骨" in rig["rig_summary"], rig["rig_summary"]


# --------------------------------------------------------------------------- #
# ① 资源面板：入口可见 + 单位下拉读得出来
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=1)
def _max_enum_items() -> int:
    """下拉条数上限的**唯一出处** = `blender_addon/unit_picker.py::MAX_ENUM_ITEMS`。

    影子加载（blender_addon 顶层 import bpy，venv 里 import 不了包 ——
    与 tests/test_unit_picker.py 同一口径）；⛔ 不在这里写死 40，免得两处漂移。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bamod_unit_picker_limit", REPO_ROOT / "blender_addon" / "unit_picker.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.MAX_ENUM_ITEMS)


def test_panel_and_import_operator_are_registered_and_units_are_listed() -> None:
    """面板/算子都在（dir() 判，⛔ 不用 hasattr），单位下拉里能找到 RU_T72B。"""
    report = _import_report()
    assert report["panel_visible"] is True
    assert "BAMOD_PT_main" in report["registered_bamod_types"]
    for operator in ("detect_game", "scan_units", "import_unit", "check_environment"):
        assert operator in report["operator_names"], report["operator_names"]
    # ⚠ 这里是**等号**不是 >100：2026-10-04 用户报「Blender 很卡」之后，动态枚举被刻意收到了
    # MAX_ENUM_ITEMS 条（回调一次重绘会被调很多次，条数直接乘进每次重绘的开销）。
    #   · 890 -> 40 **不是功能回退**：其余的全靠「筛选单位」缩小范围（用户 2026-10-11
    #     「打字打不进去」正是被这条口径 ＋ 筛选框一起救回来的，见 unit_picker.py 模块头）；
    #   · 旧断言写的是 >100（那个数来自 2026-10-03 的报告缓存：unit_items_count=890，上限之前）。
    assert report["unit_items_count"] == _max_enum_items(), report["unit_items_count"]
    assert report["unit_items_has_target"] is True
    assert report["unit_items_head"], "下拉是空的"
    # 没筛选时第一条 = 判据单位（EnumProperty 的默认值就是第一条 —— 排在 6P62 上没意义）
    assert report["unit_items_head"][0] == "RU_T72B", report["unit_items_head"]


def test_screenshot_for_the_work_order_exists() -> None:
    """工单 §8 要的那张截图：无头渲出来、非空。"""
    report = _import_report()
    render = report["render"]
    assert render["rendered"] is True, render
    path = Path(render["path"])
    assert path.is_file() and path.stat().st_size > 10_000, path
    assert path.name == f"w3_{re.sub(r'[^0-9A-Za-z_-]+', '_', report['unit_key'])}.png"


AH1Z_KEY = "US_AH-1Z Viper"
AH1Z_ASSET = "Assets/Resources_moved/ModelPrefabs/Helicopters/US_AH-1Z Viper.prefab"
#: AH-1Z 的 renderer 归属（本单实测）：9 个 renderer，但只有 3 个属于 LODGroup
#: （LOD0 = 1 = 飞行员的满模），另外 **6 个不在任何 LOD 级里**（3 个机身 LOD 是游戏
#: 自研 BaLod 在运行时选的，不是标准 LODGroup；还有两个飞行员各带一份）。
AH1Z_RENDERERS = 9
AH1Z_LOD_UNION = 3
AH1Z_OUTSIDE_LODS = 6
#: LOD0 的成员数（只有 1 个：飞行员的满模；机身的 3 个 LOD 不在标准 LODGroup 里）。
AH1Z_LOD_MEMBERS_KEEP = 1


def test_renderers_outside_every_lod_group_are_not_treated_as_lod_variants() -> None:
    """**LOD 归属**的回归：不在任何 LOD 级里的 renderer 不许被当成 LOD1+ 隐藏。

    由来：W3 第一版把「不在 LOD0 里」一律 hide_render —— T-72B_Base 的 13 个 renderer
    恰好**全部**在 LODGroup 里（outside == 0），所以那条错规则在 W3 判据上不显形。
    换 US_AH-1Z Viper 就露了：9 个 renderer 里只有 3 个属于 LODGroup，
    于是直升机只剩一个飞行员被渲出来（截图见 _work/ah1z_*.png）。
    """
    # 源码侧：先把触发条件量出来（T-72B 是 0，AH-1Z 是 6）
    assert len(_t72b_tree().renderers_outside_lods()) == 0

    ah1z = _ah1z_tree()
    assert len(ah1z.renderers) == AH1Z_RENDERERS
    assert len(ah1z.lod_union()) == AH1Z_LOD_UNION
    assert len(ah1z.renderers_outside_lods()) == AH1Z_OUTSIDE_LODS

    # Blender 侧：只有**真正的 LOD 变体**才允许被隐藏（3 - 1 = 2），不是 9 - 1 = 8
    report = _import_report(AH1Z_KEY)
    assert report["counts"]["renderers"] == AH1Z_RENDERERS
    assert report["counts"]["lod_union"] == AH1Z_LOD_UNION
    assert report["counts"]["lod_outside"] == AH1Z_OUTSIDE_LODS
    assert report["counts"]["lod_hidden"] == AH1Z_LOD_UNION - AH1Z_LOD_MEMBERS_KEEP
    assert report["import_ok"] is True


def test_units_reading_is_shared_and_bounded() -> None:
    """内存账的**回归**：3.38 GB 的包只允许在**一个短命子进程**里开，pytest 进程只读 JSON。

    为什么单独立一条：2026-10-03 的第一版把这些读数放在 pytest 进程里做，-n auto 下
    6 条用例各开一次那个包 —— 一个 worker 峰值 **10,294 MB**，机器 31 GB，直接 MemoryError，
    用户当场强制停止。这条用例钉的就是「以后不许再退回那个形状」。
    """
    reading = _units_reading()
    assert reading["units"]["RU_T72B"]["renderers"] == T72B_RENDERERS
    assert UNITS_JSON.is_file(), "读数必须落盘（跨 worker 共享 + 进程退出即释放内存）"
    assert reading["_fingerprint"] == _source_fingerprint("units", UNIT_KEY, AH1Z_KEY)
    # 读数里不可能有 UnityPy 对象（只有数字/字符串/列表），这就是内存能还回去的原因
    assert not any(
        callable(value) for value in reading["units"]["RU_T72B"].values()
    )


def test_texture_reader_refuses_a_streamed_texture_without_a_bundle() -> None:
    """负控：流式贴图既不给 bundle 也不给 stream 时必须抛，不许返回空图。"""
    tree = {
        "m_Name": "T",
        "m_Width": 4,
        "m_Height": 4,
        "m_TextureFormat": 12,
        "m_ColorSpace": 1,
        "m_StreamData": {"path": "archive:/x/CAB-y.resS", "offset": 0, "size": 16},
    }
    from ba_mod_maker.asset_kernel import texture as texture_mod

    with pytest.raises(texture_mod.StreamedTextureError):
        texture_mod.read_texture_2d(tree)
    # 正控：给了 stream 就能读
    info = texture_mod.read_texture_2d(tree, stream=lambda _info: b"\x00" * 16)
    assert info.raw == b"\x00" * 16 and info.streamed is True


# --------------------------------------------------------------------------- #
# 子进程入口：python tests/test_import_unit.py --emit-units-reading
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    if "--emit-units-reading" in sys.argv:
        raise SystemExit(_emit_units_reading())
    raise SystemExit(f"只认 --emit-units-reading（收到 {sys.argv[1:]}）")
