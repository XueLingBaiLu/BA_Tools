r"""断箭模型提取器 —— 从《断箭》里把一个模型完整取出来的小工具。

用法（给人看）：

    python ba_extract.py check                 体检：游戏 / Blender / 仓库 找没找到
    python ba_extract.py list                  列出能提取的单位模型
    python ba_extract.py list --kind turret    列出炮塔模型
    python ba_extract.py list --filter M113    只列名字里带 M113 的
    python ba_extract.py info US_M113A3        这个模型落在哪个包、要走哪些依赖
    python ba_extract.py extract US_M113A3     提取到 输出/US_M113A3/

用法（给 Agent / 脚本用 —— 全部带 --json，输出单文档 JSON）：

    python ba_extract.py list --json
    python ba_extract.py info US_M113A3 --json
    python ba_extract.py extract US_M113A3 --json

产物（extract）：

    输出/<键>/model.glb        几何 + 两套 UV + 骨骼 + 贴图（默认出）
    输出/<键>/textures/*.png   原尺寸贴图（可以直接拿去改）
    输出/<键>/scene.json       对象树：父子、顶点数、三角数、UV 层、材质、顶点组
    输出/<键>/report.json      导入器的完整读数（地址 / 包 / 未解析项）
    输出/<键>/model.blend      Blender 场景（默认出；贴图已打包，双击就开）
                             加 --no-blend 可不出

退出码：0 成功 · 1 有东西没拿到（部分成功） · 2 用不了（找不到游戏 / Blender / 这个键）

⛔ 本工具只读游戏：不写游戏目录、不改包、不装机。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "输出"
DEFAULT_GAME = Path(r"D:\Steam\steamapps\common\broken_arrow")

# Windows：界面是 提取器.cmd 用 pythonw.exe 起的 —— 那个进程**没有控制台**。此后再起一个
# **控制台程序**（venv 的 python.exe / blender.exe），Windows 会给它**新开一个控制台窗口**，
# 于是「点一个单位就闪一个黑框」（2026-10-28 实测：父 GetConsoleWindow=0；子不传标志分到
# 新控制台 657702；子传 CREATE_NO_WINDOW 则为 0）。
# ⇒ 所有子进程统一用 CREATE_NO_WINDOW 起。非 Windows 上没这个常量，取 0 等于不加参数。
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    return Path(raw) if raw else None


def find_repo() -> Path | None:
    """找数据侧仓。判据 = 有 `src/ba_mod_maker/__init__.py`；**仓名不写死**。

    2026-11-04：用户把仓改名成「断箭数据库编辑器」（此前 `BA_Mod_Maker_2.0（数据侧）`）。
    原来只按 `BA_Mod_Maker_2.0*` 前缀找 ⇒ 改名后返回 None，界面显示「仓库：✗ 找不到」。
    现在按**结构**找：在 HERE 及其往上三级的**子目录**里，谁有 `src/ba_mod_maker` 就是仓。
    （打包成 exe 后 HERE 在 `Release\<工具>\_internal`，往上三级正好是工作区根。）
    """
    env = _env_path("BA_REPO")
    cands = [env] if env else []
    #: 老路径先试（快路径 + 向后兼容）。
    for base in (HERE.parent, HERE.parent.parent):
        cands.append(base / "断箭数据库编辑器")
        cands.append(base / "BA_Mod_Maker_2.0")
    #: 名字无关：逐级往上的子目录扫一遍。
    seen: set[Path] = set()
    for base in (HERE, HERE.parent, HERE.parent.parent, HERE.parent.parent.parent):
        try:
            children = sorted(base.iterdir())
        except (OSError, AttributeError):
            continue
        for child in children:
            if child in seen or not child.is_dir():
                continue
            seen.add(child)
            cands.append(child)
    if HERE.name.startswith("BA_Mod_Maker") or HERE.name.startswith("断箭"):
        cands.append(HERE)
    for c in cands:
        if c and (c / "src" / "ba_mod_maker" / "__init__.py").is_file():
            return c
    return None


#: 2026-11-02 实测：AssetRipper 导出的 `.anim` 关键帧 `inSlope/outSlope` 会带 `-Infinity`，
#: Unity 采样这种曲线**直接饱和到终值**（实机表现＝动画一帧到位；FLYACV 挡浪板就是这个）。
#: 全库体检过 **319 个 .anim / 12,667 处**。一次体检的命令行版：`<根>\tools\fix_anim_tangents.py`。
_TANGENT_SLOPE = re.compile(r'(inSlope|outSlope): *(\{[^}]*\})')
_TANGENT_ZERO = '{x: 0, y: 0, z: 0, w: 0}'
_TANGENT_DONE: set[str] = set()


def anim_tangents_check(proj: Path, *, fix: bool = True, quiet: bool = False) -> tuple[int, int]:
    """体检并（默认）就地修导出工程里 `.anim` 的坏斜率。返回 `(坏文件数, 坏斜率处数)`。

    ⛔ 不留 `.bak`（用户明确说过不要备份垃圾）；改动是确定性的（坏斜率 → 0）。
    """
    key = str(proj)
    if key in _TANGENT_DONE:
        return (0, 0)
    _TANGENT_DONE.add(key)
    bad_files = 0
    total = 0
    root = proj / "Assets"
    if not root.is_dir():
        return (0, 0)
    for p in root.rglob('*.anim'):
        try:
            t = p.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if 'Infinity' not in t:
            continue
        n = sum(1 for m in _TANGENT_SLOPE.finditer(t) if 'Infinity' in m.group(2))
        if not n:
            continue
        bad_files += 1
        total += n
        if fix:
            t2 = _TANGENT_SLOPE.sub(
                lambda m: (m.group(1) + ': ' + _TANGENT_ZERO) if 'Infinity' in m.group(2) else m.group(0),
                t,
            )
            if t2 != t:
                p.write_text(t2, encoding='utf-8')
    if bad_files and not quiet:
        print('ANIMTANGENT 导出工程里有坏斜率（-Infinity）：%d 个 .anim / %d 处 ⇒ %s'
              % (bad_files, total, '已就地修好' if fix else '未修（fix=False）'))
    return (bad_files, total)


def exported_project() -> Path | None:
    """AssetRipper 导出的 Unity 工程（里面有真件 prefab + AnimationHub 的动画行为）。"""
    env = _env_path("BA_EXPORTED_PROJECT")
    cands = [env] if env else []
    default = Path(r"D:\断箭模组制作\参考件\Broken Arrow Assets v1.2.2.1\ExportedProject")
    cands.append(default)
    if HERE.parent.name == '断箭模组制作':
        for p in (HERE.parent / "参考件").glob("Broken Arrow Assets*/ExportedProject"):
            cands.append(p)
    for c in cands:
        if c and (c / "Assets" / "Resources_moved" / "ModelPrefabs").is_dir():
            if not _env_path('BA_NO_ANIM_FIX'):
                anim_tangents_check(c)
            return c
    return None


def find_real_prefab(key: str) -> Path | None:
    """按件名找真件 prefab（多命中且不是同名 ⇒ 返回 None，不许猜）。"""
    proj = exported_project()
    if proj is None:
        return None
    root = proj / "Assets" / "Resources_moved" / "ModelPrefabs"
    exact = sorted(root.rglob(key + '.prefab'))
    if exact:
        return exact[0]
    fuzzy = sorted(p for p in root.rglob('*.prefab') if key.lower() in p.stem.lower())
    return fuzzy[0] if len(fuzzy) == 1 else None


def find_python(repo: Path | None) -> str:
    if repo:
        venv = repo / ".venv" / "Scripts" / "python.exe"
        if venv.is_file():
            return str(venv)
    return sys.executable


def find_blender() -> Path | None:
    env = _env_path("BA_BLENDER")
    if env and env.is_file():
        return env
    for c in (Path(r"D:\Blender\blender.exe"), Path(r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe")):
        if c.is_file():
            return c
    return None


def game_root(arg: str | None) -> Path | None:
    if arg:
        p = Path(arg)
        return p if (p / "BrokenArrow.exe").is_file() else None
    env = _env_path("BA_GAME")
    if env and (env / "BrokenArrow.exe").is_file():
        return env
    return DEFAULT_GAME if (DEFAULT_GAME / "BrokenArrow.exe").is_file() else None


def data_dir(repo: Path) -> Path | None:
    """数据库明文（DataBaseCompiled）在仓库的上一级。"""
    for base in (repo.parent, repo):
        for d in sorted(base.glob("DataBaseCompiled*")):
            if (d / "Units.json").is_file():
                return d
        builds = [d for d in base.glob("DataBaseCompiled/*") if d.name.isdigit() and (d / "Units.json").is_file()]
        if builds:
            return max(builds, key=lambda d: int(d.name))
        for d in sorted(base.glob("DataBaseCompiled/*"), reverse=True):
            if (d / "Units.json").is_file():
                return d
    return None


def load_rows(folder: Path, name: str) -> list:
    p = folder / (name + ".json")
    if not p.is_file():
        return []
    d = json.loads(p.read_text(encoding="utf-8"))
    return d if isinstance(d, list) else (d.get("Rows") or d.get("rows") or [])


def catalog_info(py: str, repo: Path, game: Path | None, key: str) -> dict:
    """问产品 CLI：这个键指向哪条路径 / 哪些包（--json）。"""
    cmd = [py, "-m", "ba_mod_maker.cli", "catalog", "key", key, "--json"]
    if game:
        cmd += ["--game", str(game)]
    r = subprocess.run(
        cmd, cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return {"ok": False, "code": r.returncode, "stderr": (r.stderr or "")[-400:]}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "code": 2, "stderr": "catalog 输出不是 JSON"}


def summarize_info(raw: dict) -> dict:
    recs = raw.get("records") or []
    if not recs:
        return {"ok": False, "key": raw.get("key"), "error": "catalog 里没有这个键"}
    rec = recs[0]
    dep = rec.get("dependency") or {}
    bundles = dep.get("bundles") or []
    return {
        "ok": True,
        "key": rec.get("key"),
        "asset_path": rec.get("asset_path"),
        "resource_type": rec.get("resource_type"),
        "provider": rec.get("provider"),
        "bundle_count": len(bundles),
        "main_bundle": bundles[0].get("name") if bundles else None,
        "bundles": [b.get("name") for b in bundles],
    }


BLENDER_SCRIPT = """
import sys, os, json, re, dataclasses
REPO, GAME, KEY, OUT = {repo!r}, {game!r}, {key!r}, {out!r}
HERE, BUNDLE = {here!r}, {bundle!r}
LOD0, WANT_BLEND, WANT_GLB, WANT_FBX, WANT_ANIM = {lod0!r}, {blend!r}, {glb!r}, {fbx!r}, {anim!r}
WANT_CHANNELS = {channels!r}
PLAN = {plan!r}
PREFAB = {prefab!r}   # 真件 prefab（有就按它自动铺动画属性）
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)   # extra_channels.py 在提取器目录里
os.makedirs(OUT, exist_ok=True)
TEX = os.path.join(OUT, "textures")
os.makedirs(TEX, exist_ok=True)
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
from _ba import importer
t0 = __import__("time").time()
rep = importer.import_unit(GAME, KEY, only_lod0=LOD0)
# 对象名 -> 包里的 Mesh pathID（补顶点通道要按它配，⛔ 不按名字猜）
MESH_OF = dict(getattr(rep, "mesh_of", None) or {{}})

# ---- 装配：炮塔 / 装甲件 / 显隐 ----
from mathutils import Matrix
anchors = {{}}
for _i in range(21):
    _a = bpy.data.objects.get("turret_%d" % _i)
    if _a is not None:
        anchors[_i] = _a
assembled, assemble_errors = [], []
if PLAN.get("turrets"):
    known = {{o.name for o in bpy.data.objects}}
    for _i, _tkey in enumerate(PLAN["turrets"]):
        try:
            # ★ 2026-10-20：炮塔也要跟主单位走同一个 LOD 口径 ——
            #   这里原来没传 only_lod0（默认 False）⇒ 选「只导入 LOD0」时炮塔仍带 LOD1+，
            #   实测 --lod0 下多出 `002_skinned_turret.001` / `K1.001` / `K5.001` 三件。
            _trep = importer.import_unit(GAME, _tkey, only_lod0=LOD0)
            MESH_OF.update(getattr(_trep, "mesh_of", None) or {{}})
        except Exception as _e:  # noqa: BLE001
            assemble_errors.append(str(_tkey) + ": " + str(_e)[:160])
            continue
        _new = [o for o in bpy.data.objects if o.name not in known]
        _anchor = anchors.get(_i) or anchors.get(0)
        for _r in [o for o in _new if o.parent is None]:
            if _anchor is not None:
                _r.parent = _anchor
                _r.matrix_parent_inverse = _anchor.matrix_world.inverted()
        if _tkey in (PLAN.get("turret_hide") or ()):
            for _o in _new:
                _o.hide_viewport = True
                _o.hide_set(True)
        known = {{o.name for o in bpy.data.objects}}
        assembled.append(_tkey)

# 显隐：默认按游戏（备选装甲件隐藏）；show_all = 全点亮
if PLAN.get("show_all"):
    for _o in bpy.data.objects:
        _o.hide_viewport = False
        _o.hide_set(False)
else:
    for _nm in PLAN.get("armor_hide") or ():
        _o = bpy.data.objects.get(_nm)
        if _o is not None:
            _o.hide_viewport = True
            _o.hide_set(True)
    _show = PLAN.get("armor_show")
    if _show:
        _o = bpy.data.objects.get(_show)
        if _o is not None:
            _o.hide_viewport = False
            _o.hide_set(False)

# ---- 顶点通道补全（2026-10-25）：源 Mesh 有 Color / TexCoord2..4，
#      importer 只建 uv0/uv1 ⇒ 导出前把**源里真有的**那些补进网格。
#      失败**不挡**提取（通道是附加物，只记红一行）。
CHANNEL_ROWS, CHANNEL_SUMMARY = [], {{"objects": 0, "color": 0, "uv_extra": 0}}
if WANT_CHANNELS:
    try:
        import extra_channels
        CHANNEL_ROWS = extra_channels.apply(BUNDLE, MESH_OF)
        CHANNEL_SUMMARY = {{
            "objects": len(CHANNEL_ROWS),
            "color": sum(1 for _r in CHANNEL_ROWS if _r.color),
            "uv_extra": sum(1 for _r in CHANNEL_ROWS if _r.uvs),
        }}
    except Exception as _e:  # noqa: BLE001
        CHANNEL_SUMMARY = {{"objects": 0, "color": 0, "uv_extra": 0, "error": str(_e)[:200]}}
    print("CHANNEL_LINES " + json.dumps([_r.line() for _r in CHANNEL_ROWS], ensure_ascii=False))

# ---- 动画（2026-10-20 加）：把该件的 clip 建成真 Action 挂到骨架上，
#      打开 blend 就能按空格播。失败**不影响**提取（动画是附加物，只记一行红）。
ANIM_LINES = []
if WANT_ANIM:
    try:
        from _ba import anim_entry
        ANIM_LINES = list(anim_entry.import_actions(GAME, KEY))
    except Exception as _e:  # noqa: BLE001
        ANIM_LINES = ["红 —— 动画没导进来：" + str(_e)[:160]]
    print("ANIM_LINES " + __import__("json").dumps(ANIM_LINES, ensure_ascii=False))
    # 场景帧区间对齐到 Action 自己的范围 —— 打开 blend 按空格就能看到完整一轮
    # （不改的话默认是 1~250，60 帧的 clip 播完就停在那儿不动）。
    _acts = [a for a in bpy.data.actions if getattr(a, "frame_range", None)]
    if _acts:
        _lo = min(float(a.frame_range[0]) for a in _acts)
        _hi = max(float(a.frame_range[1]) for a in _acts)
        _sc = bpy.context.scene
        _sc.frame_start, _sc.frame_end = int(round(_lo)), int(round(_hi))
        print("ANIM_FRAMES %d %d" % (_sc.frame_start, _sc.frame_end))

elapsed = __import__("time").time() - t0

def brief(v):
    if isinstance(v, (str, int, float, bool)) or v is None: return v
    if isinstance(v, (list, tuple)): return [brief(x) for x in v][:80]
    if isinstance(v, dict): return {{str(k): brief(x) for k, x in list(v.items())[:80]}}
    return str(v)[:300]
report = {{f.name: brief(getattr(rep, f.name)) for f in dataclasses.fields(rep)}}
report["seconds"] = round(elapsed, 1)
report["extra_channels"] = [r.as_dict() for r in CHANNEL_ROWS]
open(os.path.join(OUT, "report.json"), "w", encoding="utf-8").write(
    json.dumps(report, ensure_ascii=False, indent=2, default=str))

def safe(n):
    n = n.split("__", 1)[-1] if "__" in n else n
    return re.sub("[^0-9A-Za-z._()-]+", "_", n)
tex = []
for img in list(bpy.data.images):
    if not img.size[0] or not img.size[1]: continue
    p = os.path.join(TEX, safe(img.name) + ".png")
    img.file_format = "PNG"; img.filepath_raw = p; img.save()
    img.filepath = p
    try: img.pack()
    except Exception: pass
    tex.append(os.path.basename(p))

scene = []
for o in sorted(bpy.data.objects, key=lambda x: x.name):
    row = {{"name": o.name, "type": o.type, "parent": o.parent.name if o.parent else "",
            "location": [round(v, 4) for v in o.location]}}
    if o.type == "MESH":
        row.update({{"verts": len(o.data.vertices),
                    "tris": sum(len(p.vertices) - 2 for p in o.data.polygons),
                    "uv_layers": [u.name for u in o.data.uv_layers],
                    "materials": [m.name if m else "" for m in o.data.materials],
                    "vgroups": len(o.vertex_groups)}})
    if o.type == "ARMATURE":
        row["bones"] = len(o.data.bones)
    scene.append(row)
open(os.path.join(OUT, "scene.json"), "w", encoding="utf-8").write(
    json.dumps({{"key": KEY, "objects": scene}}, ensure_ascii=False, indent=2))

made = []
if PLAN.get("preview"):
    from mathutils import Vector
    _sc = bpy.context.scene
    for _o in list(bpy.data.objects):
        if _o.type in {{"LIGHT", "CAMERA"}}:
            bpy.data.objects.remove(_o, do_unlink=True)
    def _light(_name, _loc, _energy, _size):
        _ld = bpy.data.lights.new(_name, "AREA"); _ld.energy = _energy; _ld.size = _size
        _lo = bpy.data.objects.new(_name, _ld); _sc.collection.objects.link(_lo)
        _lo.location = _loc
        _lo.rotation_euler = (Vector((0, 0, 1.2)) - _lo.location).to_track_quat("-Z", "Y").to_euler()
    _light("key", (6.5, -7.5, 8.0), 5000, 8)
    _light("fill", (-7.5, -4.0, 5.0), 1800, 10)
    _light("rim", (0.0, 8.0, 6.0), 2600, 10)
    _cd = bpy.data.cameras.new("cam"); _cd.lens = 55
    _co = bpy.data.objects.new("cam", _cd); _sc.collection.objects.link(_co); _sc.camera = _co
    _target = Vector((0, 0, 1.0))
    _co.location = _target + Vector((6.5, -8.5, 5.2))
    _co.rotation_euler = (_target - _co.location).to_track_quat("-Z", "Y").to_euler()
    _sc.render.resolution_x = 960; _sc.render.resolution_y = 720
    _sc.render.filepath = os.path.join(OUT, "preview.png")
    bpy.ops.render.render(write_still=True)
    made.append(("preview.png", os.path.getsize(_sc.render.filepath)))
if WANT_GLB:
    p = os.path.join(OUT, "model.glb")
    # ★ 2026-10-25：显式写全「顶点通道」那几个开关 —— 默认值就够，但写出来才看得出
    #   COLOR_0 / TEXCOORD_2..4 是**这几条**决定的（导出器对每条 UV 图层都出一条 TEXCOORD_n，
    #   颜色按 render_color_index 那条走）。
    bpy.ops.export_scene.gltf(
        filepath=p,
        export_format="GLB",
        export_texcoords=True,
        export_normals=True,
        export_vertex_color="ACTIVE",
        export_all_vertex_colors=True,
        export_attributes=False,
        export_skins=True,
        # ★ 2026-10-25：glb 在整条链里**只用来取节点名**（`pack unity-build` 的
        #   `_glb_mesh_node_names`）——贴图由 `textures/*.png` ＋ FBX 的 `.fbm` 带，
        #   所以这里**不导图**。副作用：导出器不再落临时图片（那条路在受限
        #   ACL 环境里会 PermissionError，实测 2026-10-25），包也小一半。
        export_image_format="NONE",
    )
    made.append(("model.glb", os.path.getsize(p)))
if WANT_FBX:
    # ★ 2026-10-25：开发者那条路 =「模型文件 -> Unity 的 ModelImporter -> Mesh 资产」。
    #   glb 没人能在 Unity 里直接导入（要装包），FBX 是 Unity **原生**支持的那一种，
    #   所以这里同时导一份 FBX 给导入器用 —— 顶点流/权重/顶点色/多套 UV 全交给 Unity 算，
    #   我们不再手写 input.bin。
    #   ⚠ 参数是实测过的：`add_leaf_bones=False`（否则会多出叶子骨）、
    #     `object_types={{'ARMATURE','MESH'}}`、`use_armature_deform_only=False`（保留全部骨，
    #     真件 m_Bones 里有非形变骨）。
    # ★ 2026-10-31（实测根因）：导出器缺省 apply_scale_options='FBX_SCALE_NONE' 把
    #   units_blender_to_fbx_factor(scene)（METRIC/scale_length=1 ⇒ 100）× global_scale(默认 1)
    #   乘进顶层导出对象的节点矩阵，顶点数据不动 ⇒ 只有 MESH/ARMATURE 那几个对象自带
    #   Lcl Scaling=100（实测 model.fbx 二进制：Armored/BaseArmor/Chassis/US_ACV_Armature/rotor），
    #   UnitScaleFactor 却是 1.0、几何 5.48 m 是对的 ⇒ Unity 里整车 100 倍。
    #   反乘 1/100 让 100×0.01=1。⛔ 别改用 FBX_SCALE_ALL（UnitScaleFactor 变 100 ⇒ 缩成 1/100）。
    #   选择集同理：动画行为挂在空物体上（Torque 的 _target=rot_blade_1），空物体默认不进 FBX
    #   ⇒ 行为被整条跳过（实测 9 条里 7 条）。共用件 fbx_export_options 里是唯一一份口径。
    import fbx_export_options
    _fbx_opts = fbx_export_options.apply(bpy.context.scene)
    p = os.path.join(OUT, "model.fbx")
    bpy.ops.export_scene.fbx(
        filepath=p,
        add_leaf_bones=False,
        bake_anim=False,
        path_mode='COPY',
        embed_textures=False,
        use_armature_deform_only=False,
        mesh_smooth_type='FACE',
        **_fbx_opts,
    )
    made.append(("model.fbx", os.path.getsize(p)))
# ★ 2026-10-29：**提取完就把动画属性铺进 .blend**（不用再手工跑第二跳）。
#   属性来源 = 真件 prefab 的 AnimationHub（PREFAB 由主机侧按件名查出来）。
if PREFAB and os.path.isfile(PREFAB):
    try:
        import anim_import, anim_props
        _payload = anim_import.load_props(PREFAB)
        _applied, _missing = anim_props.apply_props(_payload.get("props") or [])
        print("ANIMPROPS auto props=%d missing=%d prefab=%s"
              % (_applied, len(_missing), os.path.basename(PREFAB)))
        for _m in sorted(set(_missing))[:8]:
            print("ANIMPROPS auto 缺物体: " + _m)
    except Exception as _exc:  # ⛔ 不许因为铺属性失败把提取整条搞挂
        print("ANIMPROPS auto FAIL " + type(_exc).__name__ + ": " + str(_exc))
if WANT_BLEND:
    p = os.path.join(OUT, "model.blend")
    bpy.ops.wm.save_as_mainfile(filepath=p)
    made.append(("model.blend", os.path.getsize(p)))
print("EXTRACT_OK " + json.dumps({{"out": OUT, "seconds": round(elapsed, 1),
      "channels": CHANNEL_SUMMARY,
      "assembled": assembled, "assemble_errors": assemble_errors,
      "meshes": sum(1 for o in bpy.data.objects if o.type == "MESH"),
      "armatures": sum(1 for o in bpy.data.objects if o.type == "ARMATURE"),
      "materials": len(bpy.data.materials), "images": len(tex),
      "files": made}}))
"""


def temp_root() -> Path:
    """一个确定可写的临时根（⛔ 别让它落到工作目录里：tempfile 找不到 TEMP 时就会那么干）。"""
    for cand in (os.environ.get("TEMP"), os.environ.get("TMP"), str(Path.home() / "AppData" / "Local" / "Temp")):
        if not cand:
            continue
        p = Path(cand)
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".ba_write_probe"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
            return p
        except OSError:
            continue
    fallback = OUT_DIR / ".tmp"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _run_blender_once(blender: Path, script: str, started: float) -> tuple[dict | None, str, int]:
    """跑一次 Blender（后台、无控制台也稳）。返回 (解析结果 | None, 日志尾巴, 退出码)。"""
    job_dir = Path(tempfile.mkdtemp(prefix="ba_extract_", dir=str(temp_root())))
    tmp = job_dir / "job.py"
    tmp.write_text(script, encoding="utf-8")
    cmd = [str(blender), "-b", "--factory-startup", "--python", str(tmp)]
    # ★ 2026-10-18（用户现场：GUI 里起 Blender 报
    #   「Could not generate a temp file name for …\Temp\blender_a39140」）：
    #   **把 Blender 自己的临时目录也钉在 job_dir 里** —— 它就不再依赖系统 TEMP
    #   （实测那台机器 TEMP 里有 2,164 个文件 / 145 个旧 blender_* 目录），
    #   而且跟 job.py 一起被 rmtree 掉。
    env = dict(os.environ)
    for var in ("TEMP", "TMP", "TMPDIR"):
        env[var] = str(job_dir)
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(job_dir),
            creationflags=NO_WINDOW,
        )
        out = r.stdout or ""
        code = r.returncode
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)  # ⛔ 临时脚本用完就删（2026-10-18 用户发现遗留）
    tail = "\n".join(out.splitlines()[-10:])
    line = next((l for l in out.splitlines() if l.startswith("EXTRACT_OK ")), None)
    if line is None:
        return None, tail, code
    data = json.loads(line[len("EXTRACT_OK "):])
    data.update({"ok": True, "wall_seconds": round(time.time() - started, 1)})
    return data, tail, code


def run_extract(
    py: str,
    repo: Path,
    game: Path,
    key: str,
    out: Path,
    lod0: bool,
    blend: bool,
    glb: bool,
    fbx: bool = True,
    plan: dict | None = None,
    anim: bool = True,
    bundle: str = "",
    channels: bool = True,
) -> dict:
    blender = find_blender()
    if blender is None:
        return {"ok": False, "error": "找不到 Blender（设 BA_BLENDER 指向 blender.exe）"}
    prefab = find_real_prefab(key)
    script = BLENDER_SCRIPT.format(repo=str(repo), game=str(game), key=key, out=str(out),
                                   here=str(HERE), bundle=str(bundle or ""),
                                   lod0=lod0, blend=blend, glb=glb, fbx=fbx, plan=plan or {},
                                   anim=anim, channels=channels,
                                   prefab=str(prefab or ""))
    started = time.time()
    last: tuple[dict | None, str, int] = (None, "", 0)
    for attempt in range(2):  # 第一次没出 EXTRACT_OK 就换一间干净临时目录再试一次
        last = _run_blender_once(blender, script, started)
        if last[0] is not None:
            return last[0]
        if attempt == 0:
            time.sleep(1.0)
    data, tail, code = last
    return {"ok": False, "error": "Blender 没跑通（试了 2 次）", "tail": tail, "code": code}


#: ①类：代码硬编码的节点名（改错 = 静默失效）。
CONTRACT_HARD = (
    ("Chassis", r"^Chassis$", "护甲/附加模型/机动的容器：逐字、大小写敏感、必须是根的直接子"),
    ("BaseArmor", r"^BaseArmor$", "默认装甲壳；必须是 Chassis 的直接子"),
    ("BaseAbility", r"^BaseAbility$", "附加能力件；必须是 Chassis 的直接子"),
    ("BaseMobility", r"^BaseMobility$", "附加机部件；必须是 Chassis 的直接子"),
    ("root", r"^root$", "骨骼容器（ToLower 比较，大小写不敏感）"),
    ("body", r"^body$", "车体骨骼容器（ToLower 比较）"),
    ("turret_N", r"^turret_\d+$", "炮塔位：代码拼 turret_ + 序号"),
    ("weapon_N", r"^weapon_\d+$", "武器位（PPtr 引用；代码不按名字找，但位置靠它）"),
    ("LR0xx / RR0xx", r"^(LR|RR)0\d+$", "车轮：只比前 3 字符，长度须 > 3"),
    ("L0xx / R0xx", r"^L0\d+$|^R0\d+$", "悬挂：只比前 2 字符，长度须 > 3"),
    ("Entrances", r"^Entrances$", "登机点容器"),
    ("Seats", r"^Seats$", "座位容器"),
    ("VFXPoint_*", r"^VFXPoint_", "特效挂点（递归精确名）"),
)


def _rip_root() -> Path | None:
    env = _env_path("BA_RIP")
    cands = [env] if env else []
    cands += [HERE.parent / "unity-ba-rip-all", HERE.parent / "ba-clean-assets"]
    for c in cands:
        if c and (c / "Assets").is_dir():
            return c
    return None


def contract_rows(key: str, game: Path | None, unit_id: int | None) -> dict:
    """把这台车的节点名契约生成出来（读提取树或已提取的 scene.json）。"""
    repo = find_repo()
    if repo is None:
        return {"ok": False, "error": "找不到仓库"}
    info = summarize_info(catalog_info(find_python(repo), repo, game, key))
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error")}
    rip = _rip_root()
    names: list[str] = []
    source = ""
    if rip:
        prefab = rip / info["asset_path"].replace("/", os.sep)
        if prefab.is_file():
            text = prefab.read_text(encoding="utf-8", errors="ignore")
            names = [m.group(1).strip() for m in re.finditer(r"(?m)^  m_Name: (.*)$", text)]
            source = str(prefab)
    if not names:
        scene = OUT_DIR / key.replace("/", "_") / "scene.json"
        if scene.is_file():
            data = json.loads(scene.read_text(encoding="utf-8"))
            names = [o["name"] for o in data.get("objects", [])]
            source = str(scene)
    names = [n for n in names if n]
    if not names:
        return {"ok": False, "error": "拿不到节点名：先 extract 一次，或设 BA_RIP 指向提取树"}
    hard = []
    for label, pat, note in CONTRACT_HARD:
        got = [n for n in names if re.match(pat, n)]
        hard.append({"name": label, "count": len(got), "found": got[:8], "note": note, "ok": bool(got)})
    from_data = []
    dd = data_dir(repo)
    if dd and unit_id is not None:
        arms = {a["Id"]: a for a in load_rows(dd, "Armors")}
        for r in load_rows(dd, "UnitArmors"):
            if r.get("UnitId") != unit_id:
                continue
            a = arms.get(r.get("ArmorId"), {})
            fn = a.get("ModelFileName")
            if fn:
                from_data.append({"name": fn, "from": a.get("Name"), "ok": fn in names})
    return {"ok": True, "key": key, "source": source, "nodes": len(names),
            "hard": hard, "from_data": from_data}


def cmd_contract(args) -> int:
    row = contract_rows(args.key, game_root(args.game), args.unit_id)
    if args.json:
        print(json.dumps(row, ensure_ascii=False, indent=2))
        return 0 if row.get("ok") else 2
    if not row.get("ok"):
        print("✗ " + str(row.get("error")), file=sys.stderr)
        return 2
    print("节点名契约：%s" % row["key"])
    print("来源：%s（%d 个节点）" % (row["source"], row["nodes"]))
    print()
    print("① 代码硬编码（改错就静默失效）")
    for r in row["hard"]:
        if r["ok"]:
            print("   ✔ %-16s %d 个  %s" % (r["name"], r["count"], ", ".join(r["found"][:4])))
        else:
            print("   · %-16s （这台车没有这一类）" % r["name"])
    if row["from_data"]:
        print()
        print("② 数据库指向（这台车的改装件要求这些名字）")
        for r in row["from_data"]:
            print("   %s %-16s ← %s" % ("✔" if r["ok"] else "✗", r["name"], r["from"]))
    return 0


def cmd_check(args) -> int:
    repo = find_repo()
    py = find_python(repo)
    blender = find_blender()
    game = game_root(args.game)
    dd = data_dir(repo) if repo else None
    out = {
        "version": VERSION,
        "repo": str(repo) if repo else None,
        "python": py,
        "blender": str(blender) if blender else None,
        "game": str(game) if game else None,
        "catalog": str(game / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "catalog.bin") if game else None,
        "data": str(dd) if dd else None,
    }
    ok = bool(repo and blender and game)
    out["ok"] = ok
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for k, v in out.items():
            print("%-9s %s" % (k, v))
        print("\n" + ("✅ 可以提取" if ok else "✗ 缺东西（见上面哪一项是 None）"))
    return 0 if ok else 2


def cmd_list(args) -> int:
    repo = find_repo()
    if repo is None:
        print("找不到仓库（数据侧仓，现名「断箭数据库编辑器」）", file=sys.stderr)
        return 2
    dd = data_dir(repo)
    if dd is None:
        print("找不到 DataBaseCompiled", file=sys.stderr)
        return 2
    rows_out = []
    if args.kind in ("unit", "all"):
        for u in load_rows(dd, "Units"):
            k = u.get("ModelFileName")
            if not k:
                continue
            rows_out.append({"kind": "unit", "key": k, "id": u.get("Id"),
                             "hud_name": u.get("HUDName"), "name": u.get("Name")})
    if args.kind in ("turret", "all"):
        for t in load_rows(dd, "Turrets"):
            k = t.get("ModelFileName")
            if not k:
                continue
            rows_out.append({"kind": "turret", "key": k, "id": t.get("Id"),
                             "hud_name": None, "name": t.get("Name")})
    if args.filter:
        f = args.filter.lower()
        rows_out = [r for r in rows_out if f in (r["key"] or "").lower() or f in str(r["name"] or "").lower() or f in str(r["hud_name"] or "").lower()]
    if args.json:
        print(json.dumps({"ok": True, "count": len(rows_out), "items": rows_out}, ensure_ascii=False, indent=2))
        return 0
    print("共 %d 个模型（%s）" % (len(rows_out), dd.name))
    for r in rows_out:
        label = r["hud_name"] or r["name"] or ""
        print("  %-8s %-44s %s" % (r["kind"], r["key"], label))
    return 0


def cmd_info(args) -> int:
    repo = find_repo()
    if repo is None:
        print("找不到仓库", file=sys.stderr)
        return 2
    py = find_python(repo)
    game = game_root(args.game)
    info = summarize_info(catalog_info(py, repo, game, args.key))
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0 if info.get("ok") else 2
    if not info.get("ok"):
        print("✗ " + str(info.get("error")))
        return 2
    print("键        ", info["key"])
    print("资产路径  ", info["asset_path"])
    print("类型      ", info["resource_type"])
    print("主包      ", info["main_bundle"])
    print("依赖组    %d 个包" % info["bundle_count"])
    return 0


def unit_plan(
    dd: Path | None,
    key: str,
    *,
    turrets: bool = True,
    all_turrets: bool = False,
    armor: bool = True,
    show_all: bool = False,
    preview: bool = False,
) -> dict:
    """从数据库给这台车算一份装配计划（默认装的 / 备选的 / 显隐）。"""
    plan: dict = {
        "turrets": [],
        "turret_hide": [],
        "armor_show": None,
        "armor_hide": [],
        "show_all": show_all,
        "preview": preview,
    }
    if dd is None:
        return plan
    units = [u for u in load_rows(dd, "Units") if u.get("ModelFileName") == key]
    if not units:
        return plan
    uid = units[0].get("Id")
    if turrets:
        turs = {t["Id"]: t for t in load_rows(dd, "Turrets")}
        rows = sorted(
            (r for r in load_rows(dd, "TurretUnits") if r.get("UnitId") == uid),
            key=lambda r: (r.get("Order") or 0, r.get("Id") or 0),
        )
        picked: list[tuple[bool, str]] = []
        seen: set[str] = set()
        for r in rows:
            t = turs.get(r.get("TurretId")) or {}
            k = t.get("ModelFileName")
            if not k or k in seen:
                continue
            seen.add(k)
            picked.append((bool(t.get("IsDefault")), k))
        default = next((k for is_def, k in picked if is_def), picked[0][1] if picked else None)
        if default:
            plan["turrets"].append(default)
        if all_turrets:
            for _is_def, k in picked:
                if k != default:
                    plan["turrets"].append(k)
                    plan["turret_hide"].append(k)
    if armor:
        arms = {a["Id"]: a for a in load_rows(dd, "Armors")}
        names: list[str] = []
        chosen: str | None = None
        for r in load_rows(dd, "UnitArmors"):
            if r.get("UnitId") != uid:
                continue
            a = arms.get(r.get("ArmorId")) or {}
            n = a.get("ModelFileName")
            if a.get("IsDefault") and n:
                chosen = n
            if n:
                names.append(n)
        plan["armor_show"] = chosen
        plan["armor_hide"] = [n for n in dict.fromkeys(names) if n != chosen]
    return plan


def cmd_extract(args) -> int:
    repo = find_repo()
    if repo is None:
        print("找不到仓库", file=sys.stderr)
        return 2
    py = find_python(repo)
    game = game_root(args.game)
    if game is None:
        print("找不到游戏（用 --game 指，或设 BA_GAME）", file=sys.stderr)
        return 2
    info = summarize_info(catalog_info(py, repo, game, args.key))
    if not info.get("ok"):
        print("✗ " + str(info.get("error")), file=sys.stderr)
        return 2
    out = Path(args.out).resolve() if args.out else (OUT_DIR / args.key.replace("/", "_"))
    plan = unit_plan(
        data_dir(repo),
        args.key,
        turrets=not args.no_turrets,
        all_turrets=args.all_turrets,
        armor=not args.no_armor,
        show_all=args.show_all,
        preview=args.preview,
    )
    # 主包（补 Color / TexCoord2..4 要在 Blender 里再开一次它；asset_dump 也用这一条）
    bundles = (info or {}).get("bundles") or []
    bpath = (Path(game) / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "PC" / str(bundles[0])
             ) if bundles else None
    result = run_extract(
        py, repo, game, args.key, out, args.lod0, not args.no_blend, not args.no_glb,
        plan=plan,
        fbx=not getattr(args, "no_fbx", False),
        anim=not args.no_anim,
        bundle=str(bpath) if bpath is not None else "",
        channels=not getattr(args, "no_extra_channels", False),
    )
    if args.json:
        print(json.dumps({"ok": result.get("ok"), "info": info, "extract": result}, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2
    if not result.get("ok"):
        print("✗ " + str(result.get("error")), file=sys.stderr)
        if result.get("tail"):
            print(result["tail"][-800:], file=sys.stderr)
        return 2
    if result.get("assembled"):
        print("   装配：%s" % ", ".join(result["assembled"]))
    for err in result.get("assemble_errors") or ():
        print("   ⚠ 装配失败：%s" % err)
    # ---- 完整资产：prefab 对象图 + 网格二进制（用仓库 venv 跑，UnityPy 在那儿）----
    asset_note = ""
    dump = Path(__file__).resolve().parent / "asset_dump.py"
    if bpath is not None and bpath.is_file() and dump.is_file() and py:
        try:
            r = subprocess.run(
                [py, str(dump), str(bpath), args.key, str(out)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800,
                creationflags=NO_WINDOW,
            )
            line = next((x for x in (r.stdout or "").splitlines() if x.startswith("ASSET_OK")), "")
            asset_note = line.replace("ASSET_OK ", "")
        except Exception as exc:  # noqa: BLE001
            asset_note = "失败 %s" % type(exc).__name__
    print("✅ 提取完成：%s" % out)
    print("   网格 %d · 骨架 %d · 材质 %d · 贴图 %d · 用时 %.1fs" % (
        result["meshes"], result["armatures"], result["materials"], result["images"], result["seconds"]))
    ch = result.get("channels") or {}
    if ch:
        print("   顶点通道：color %d 件 · TexCoord2/3/4 %d 件 · 共 %d 个网格%s" % (
            int(ch.get("color") or 0), int(ch.get("uv_extra") or 0), int(ch.get("objects") or 0),
            ("  ⚠ " + str(ch["error"])) if ch.get("error") else ""))
    for name, size in result["files"]:
        print("   %-12s %.1f MB" % (name, size / 1e6))
    if asset_note:
        print("   asset/    %s" % asset_note)
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="ba_extract", description="断箭模型提取器 v" + VERSION)
    ap.add_argument("--game", help="游戏根目录（含 BrokenArrow.exe）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="体检：游戏 / Blender / 仓库")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("list", help="列出能提取的模型")
    p.add_argument("--kind", choices=("unit", "turret", "all"), default="unit")
    p.add_argument("--filter", default="", help="只列名字里含这些字的")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("info", help="这个模型落在哪、要走哪些包")
    p.add_argument("key")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("extract", help="提取一个模型到文件夹")
    p.add_argument("key")
    p.add_argument("-o", "--out", help="输出目录（默认 输出/<键>）")
    p.add_argument("--lod0", action="store_true", help="只要 LOD0（快，但改装件可能不在）")
    p.add_argument("--no-blend", action="store_true", help="不出 .blend（默认出：提取出来是要看/改的）")
    p.add_argument("--no-turrets", action="store_true", help="不装配炮塔")
    p.add_argument("--all-turrets", action="store_true", help="备选炮塔也导进来（默认隐藏）")
    p.add_argument("--no-armor", action="store_true", help="不按数据库处理装甲件显隐")
    p.add_argument("--show-all", action="store_true", help="所有隐藏件都点亮（默认按游戏）")
    p.add_argument("--preview", action="store_true", help="额外渲一张 preview.png（默认不出：自己开 Blender 看）")
    p.add_argument("--no-glb", action="store_true", help="不出 GLB")
    p.add_argument("--no-fbx", action="store_true",
                   help="不出 FBX（默认出：导入器走 Unity 原生 ModelImporter，见 2026-10-25 的路线改造）")
    p.add_argument("--no-extra-channels", action="store_true",
                   help="不补 Color / TexCoord2..4（默认补：源 Mesh 里有就写进网格）")
    p.add_argument("--no-anim", action="store_true",
                   help="不把动画建成 Action（默认建，打开 blend 就能播）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_extract)


    p = sub.add_parser("contract", help="列出这个模型必须有的节点名（契约速查）")
    p.add_argument("key")
    p.add_argument("--unit-id", type=int, default=None, help="Units.Id：给了就一并查这台车的改装件名字")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_contract)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
