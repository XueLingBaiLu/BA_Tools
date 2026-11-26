"""Unity 造 prefab 包 —— Python 侧（只产 `job.json`；网格交给 Unity 的 ModelImporter）。

规格（C# 与这里共用同一份合同，⛔ 一个字节都别自作主张改）：
`_work/unitybuild/SPEC-unity-造prefab包-v1.md`。

两步（产品**自己不启 Unity** —— AGENTS §0.17）：
  1) :func:`build_job`  工程（`model.glb` 给节点名 + `model.fbx` 给网格）-> `job.json`；
     Unity 由用户/Agent 用 DSH 工具直接起：`-executeMethod UnitBuild.Run`；
  2) C# 侧用 `ModelImporter` 导入 FBX -> 换 sharedMesh -> 存 prefab -> 打 AssetBundle
     -> 写 `report.json`（本模块只读报告）。

## 三条口径（都是 2026-10-25 实测出来的，别凭印象改）

1. **只发 LOD0 那几件**：真件 `RU_T72B_Base.prefab` 的 LODGroup 4 档（0.1 / 0.04 / 0.005 /
   0.0015），LOD1/LOD2 的渲染器（`000_skinned_Chassis` 等）由 C# 侧 `lod=true` 的同槽扩张
   覆盖。把它们也塞进 `parts` 会让同一 LOD 槽位被写两次（结果取决于顺序）。
2. **网格只有一条路**：`model.fbx` 交给 Unity 的 `ModelImporter` 造 Mesh 资产。
   ⛔ 没有 `model.fbx` 直接抛（`glb` / `OBJ` 不行）；Python 手写网格字节那条旧路
   （`encode_bin` 那一串）2026-10-25 已**整体退役**，⛔ 不留兼容 shim、不降级。
3. **`model.glb` 仍要，但只取节点名**：:func:`_glb_mesh_node_names` 拿它去对真件 prefab 的
   渲染器名，几何一个顶点都不读。骨名 / 权重 / uv1 / 顶点色 / 多套 UV 全由 C# 侧
   `UnitBuild.MeshFromFbx` 按**骨名**重排到真件的 `m_Bones`（⛔ 不按骨下标猜）。
"""
from __future__ import annotations

import json
import os
import re
import struct
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ba_mod_maker.core.assets import (
    behaviour_stubs,
    material_keywords,
    material_values,
    shader_bind,
)
from ba_mod_maker.core.errors import AssetError

# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

__all__ = [
    "DEFAULT_UNITY_PROJECT", "DEFAULT_BUNDLE_OUT", "DEFAULT_UNITY_EXE",
    "UnityJobError", "build_job", "find_prefab", "read_prefab",
    "unity_command", "unity_project_path",
]


#: 规格 §1 钉死的绝对路径（可用参数或环境变量覆盖）
DEFAULT_UNITY_PROJECT = Path(os.environ.get("UNITBUILD_UNITY_PROJECT") or
                             r"D:\断箭模组制作\参考件\Broken Arrow Assets v1.2.2.1\ExportedProject")
DEFAULT_UNITY_EXE = Path(r"D:\Unity\6000.3.24f1\Editor\Unity.exe")
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_BUNDLE_OUT = WORK / "unitybuild" / "out"


_CLASS_SMR = 137
_CLASS_MESH_RENDERER = 23
_RENDERER_CLASSES = {_CLASS_SMR: "SkinnedMeshRenderer", _CLASS_MESH_RENDERER: "MeshRenderer"}
_CLASS_GAMEOBJECT = 1
#: ★ 2026-10-27：MeshRenderer 自己**没有** `m_Mesh` —— 网格在同一个 GameObject 的
#: MeshFilter（classID 33）上。
#: 少了这一条，非蒙皮件（例 M1A2 SEP v3 的 `US_M2_Browning …` / `M240C_box`）的
#: `mesh_guid` 永远是空，
#: 「同名多件共享同一份原网格」这条判据就永远走不到。
_CLASS_MESHFILTER = 33
_CLASS_TRANSFORM = (4, 224)
_CLASS_LODGROUP = 205
_DOC_SPLIT = re.compile(r"^--- !u!(\d+) &(-?\d+).*$", re.M)


class UnityJobError(AssetError):
    """job 造不出来（真件 prefab 读不到、没有 model.fbx、节点名对不上）—— ⛔ 不许降级通过。"""


# --------------------------------------------------------------------------- #
# glb 的 JSON 块（只取节点名；几何一个顶点都不读）
# --------------------------------------------------------------------------- #

def _glb_chunks(glb: str | Path) -> tuple[dict[str, Any], bytes]:
    """一份 GLB -> (glTF JSON, BIN 块)。只认 2.0。"""
    data = Path(glb).read_bytes()
    if len(data) < 12 or data[:4] != b"glTF":
        raise UnityJobError(f"{Path(glb).name} 不是 GLB（头 4 字节不是 glTF）")
    _magic, version, length = struct.unpack_from("<4sII", data, 0)
    if version != 2:
        raise UnityJobError(f"{Path(glb).name} 是 glTF {version}，只认 2.0")
    pos, doc, blob = 12, None, b""
    while pos + 8 <= min(length, len(data)):
        clen, ctype = struct.unpack_from("<I4s", data, pos)
        body = data[pos + 8: pos + 8 + clen]
        if ctype == b"JSON":
            doc = json.loads(body.decode("utf-8"))
        elif ctype == b"BIN\x00":
            blob = body
        pos += 8 + clen
    if doc is None:
        raise UnityJobError(f"{Path(glb).name} 没有 JSON 块")
    return doc, blob


# --------------------------------------------------------------------------- #
# 真件 prefab（导出工程里的 YAML 文本）—— 只读节点名 / 渲染器 / 骨名 / LOD 档
# --------------------------------------------------------------------------- #


def _field(body: str, key: str) -> str:
    m = re.search(r"^  " + re.escape(key) + r":(.*)$", body, re.M)
    return m.group(1).strip() if m else ""


def _ref(body: str, key: str) -> int | None:
    m = re.match(r"\{fileID: (-?\d+)", _field(body, key))
    return int(m.group(1)) if m else None


def _block(body: str, key: str) -> str:
    """取 `  <key>:` 之后、下一条 `  m_Xxx:` 之前的整块（YAML 文本，不是真 YAML 解析器）。"""
    m = re.search(r"^  " + re.escape(key) + r":\s*$", body, re.M)
    if not m:
        return ""
    rest = body[m.end():]
    nxt = re.search(r"^  m_[A-Za-z0-9]+:", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def _split_docs(text: str) -> list[tuple[int, int, str]]:
    marks = list(_DOC_SPLIT.finditer(text))
    out: list[tuple[int, int, str]] = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((int(mark.group(1)), int(mark.group(2)), text[mark.end():end]))
    return out


def _transform_path(go_fid: int | None, tr_go: dict[int, int],
                    tr_father: dict[int, int], go_name: dict[int, str]) -> str:
    """GameObject 的层级路径（`A/B/C`），只用来看 / 报错，不参与匹配。"""
    if go_fid is None:
        return ""
    go_to_tr = {go: tr for tr, go in tr_go.items()}
    parts: list[str] = []
    cur: int | None = go_fid
    seen: set[int] = set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        parts.append(go_name.get(cur, ""))
        tr = go_to_tr.get(cur)
        father_tr = tr_father.get(tr, 0) if tr is not None else 0
        cur = tr_go.get(father_tr) if father_tr else None
    return "/".join(reversed(parts))


def read_prefab(prefab_file: str | Path) -> dict[str, Any]:
    """读导出工程里的 prefab（YAML 文本）-> 渲染器节点表。

    返回 ``{"file", "renderers": [{class_id, name, path, bones, materials, mesh_guid,
    lod_level}], "lod_levels": [[名字…]…], "game_objects": {fileID: 名字}}``；
    `bones[i]` 是 `m_Bones[i]` 的骨头名（`{fileID: 0}` 的空槽 = `None`）。

    ⛔ 读不到 / 不是文本 YAML ⇒ 抛（不许退化成「没有骨名」）。
    """
    path = Path(prefab_file)
    if not path.is_file():
        raise UnityJobError(
            f"真件 prefab 不存在：{path}",
            hint="导出工程在 `D:\\断箭模组制作\\参考件\\Broken Arrow Assets v1.2.2.1\\ExportedProject`；"
                 "prefab 资产路径形如 Assets/Resources_moved/ModelPrefabs/RU/RU_T72B_Base.prefab")
    raw = path.read_bytes()
    if b"%YAML" not in raw[:64]:
        raise UnityJobError(
            f"{path.name} 不是文本 YAML prefab（导出工程要 Text 序列化）",
            hint="这把尺子靠读 YAML 文本拿节点名与骨名；二进制 prefab 请重新导出")
    text = raw.decode("utf-8", "replace")
    docs = _split_docs(text)
    if not docs:
        raise UnityJobError(f"{path.name} 里一个 Unity 对象都没有")
    go_name: dict[int, str] = {}
    tr_go: dict[int, int] = {}
    tr_father: dict[int, int] = {}
    mf_mesh: dict[int, str] = {}          # GameObject -> MeshFilter 的 m_Mesh guid
    for cls, fid, body in docs:
        if cls == _CLASS_MESHFILTER:
            owner = _ref(body, "m_GameObject")
            mmf = re.search(r"m_Mesh: \{fileID: -?\d+, guid: ([0-9a-f]+)", body)
            if owner is not None and mmf:
                mf_mesh[owner] = mmf.group(1)
            continue
        if cls == _CLASS_GAMEOBJECT:
            go_name[fid] = _field(body, "m_Name")
        elif cls in _CLASS_TRANSFORM:
            owner = _ref(body, "m_GameObject")
            if owner is not None:
                tr_go[fid] = owner
            father = _ref(body, "m_Father")
            if father is not None:
                tr_father[fid] = father
    lod_levels_raw: list[list[int]] = []
    lod_by_component: dict[int, int] = {}
    for cls, _fid, body in docs:
        if cls != _CLASS_LODGROUP:
            continue
        chunks = re.split(r"^  - screenRelativeHeight:", _block(body, "m_LODs"), flags=re.M)
        for level, chunk in enumerate(chunks[1:]):
            refs = [int(x) for x in re.findall(r"- renderer: \{fileID: (-?\d+)\}", chunk)]
            lod_levels_raw.append(refs)
            for ref in refs:
                lod_by_component.setdefault(ref, level)
    renderers: list[dict[str, Any]] = []
    for cls, fid, body in docs:
        if cls not in _RENDERER_CLASSES:
            continue
        owner = _ref(body, "m_GameObject")
        name = go_name.get(owner, "") if owner is not None else ""
        bones: list[str | None] = []
        if cls == _CLASS_SMR:
            for bone_ref in re.findall(r"\{fileID: (-?\d+)\}", _block(body, "m_Bones")):
                bid = int(bone_ref)
                bones.append(go_name.get(tr_go.get(bid, -1)) if bid else None)
        mesh_guid = ""
        mm = re.search(r"m_Mesh: \{fileID: -?\d+, guid: ([0-9a-f]+)", body)
        if mm:
            mesh_guid = mm.group(1)
        elif cls == _CLASS_MESH_RENDERER and owner is not None:
            mesh_guid = mf_mesh.get(owner, "")
        renderers.append({
            "class_id": cls,
            "class_name": _RENDERER_CLASSES[cls],
            "component": fid,
            "name": name,
            "path": _transform_path(owner, tr_go, tr_father, go_name),
            "bones": bones,
            "materials": len(re.findall(r"- \{fileID: -?\d+, guid:",
                                       _block(body, "m_Materials"))),
            "mesh_guid": mesh_guid,
            "lod_level": lod_by_component.get(fid),
        })
    return {
        "file": str(path),
        "game_objects": go_name,
        "renderers": renderers,
        "lod_levels": lod_levels_raw,
    }


def unity_project_path(explicit: str | Path | None = None) -> Path:
    """导出工程根（规格 §1 钉死；`--unity-project` / `UNITBUILD_UNITY_PROJECT` 可覆盖）。"""
    path = Path(explicit) if explicit else DEFAULT_UNITY_PROJECT
    if not path.is_dir():
        raise UnityJobError(
            f"Unity 导出工程不存在：{path}",
            hint="规格 §1 钉死在 `D:\\断箭模组制作\\参考件\\Broken Arrow Assets v1.2.2.1\\ExportedProject`；"
                 "用 --unity-project 指定别的")
    return path


def find_prefab(unity_project: str | Path, base_unit: str) -> str:
    """按单位键找真件 prefab 的**资产路径**（`Assets/…/RU_T72B_Base.prefab`）。

    先找 `<key>_Base.prefab` 再找 `<key>.prefab`；0 个 / 多个都抛（⛔ 不猜）。
    """
    root = Path(unity_project)
    base = root / "Assets" / "Resources_moved" / "ModelPrefabs"
    if not base.is_dir():
        raise UnityJobError(f"导出工程里没有 Assets/Resources_moved/ModelPrefabs：{root}")
    for pattern in (f"{base_unit}_Base.prefab", f"{base_unit}.prefab"):
        hits = sorted(base.rglob(pattern))
        if len(hits) == 1:
            return hits[0].relative_to(root).as_posix()
        if len(hits) > 1:
            raise UnityJobError(
                f"按 `{pattern}` 找到 {len(hits)} 个 prefab —— 不唯一，请用 --prefab 点名",
                hint="候选：" + "、".join(h.relative_to(root).as_posix() for h in hits[:6]))
    near = sorted(p.name for p in base.rglob("*" + str(base_unit) + "*.prefab"))
    raise UnityJobError(
        f"导出工程里没有叫 {base_unit!r} 的 prefab",
        hint=(("相近的名字：" + "、".join(near[:8])) if near
              else "核对 Units.ModelFileName（例 RU_T72B）"))


# --------------------------------------------------------------------------- #
# job（规格 §2）
# --------------------------------------------------------------------------- #


def _resolve_prefab(prefab_path: str, unity_project: Path) -> tuple[str, Path]:
    """`prefab_path`（资产路径或绝对路径）-> (资产路径, 磁盘文件)。"""
    raw = str(prefab_path or "").strip().replace("\\", "/")
    if not raw:
        raise UnityJobError("prefab_path 空着")
    candidate = Path(raw)
    if candidate.is_absolute():
        if not candidate.is_file():
            raise UnityJobError(f"真件 prefab 不存在：{candidate}")
        try:
            return (candidate.resolve().relative_to(unity_project.resolve()).as_posix(),
                    candidate)
        except ValueError:
            return candidate.as_posix(), candidate
    file = unity_project / raw
    if not file.is_file():
        raise UnityJobError(f"导出工程里没有这个 prefab：{raw}")
    return raw, file


def unity_command(unity_project: str | Path, job_path: str | Path,
                  log_path: str | Path | None = None) -> str:
    """规格 §6 的起法（**打印给人 / Agent 抄**；产品自己不启 Unity）。"""
    log = Path(log_path) if log_path else Path(job_path).with_name("unity.log")
    return ('& "' + str(DEFAULT_UNITY_EXE) + '" -batchmode -quit '
            + '-projectPath "' + str(Path(unity_project).resolve()) + '" '
            + '-executeMethod UnitBuild.Run -logFile "' + str(log.resolve()) + '"')


def _skip_reason(name: str, lod0_hits: list[dict[str, Any]],
                 lod_hits: list[dict[str, Any]], lod: bool) -> str:
    if len(lod0_hits) > 1:
        return ("真件 prefab 里同名渲染器 " + str(len(lod0_hits)) + " 个（"
                + "、".join(h["path"] for h in lod0_hits[:4]) + "），按名字无法定位")
    if lod_hits:
        levels = sorted({int(h["lod_level"]) for h in lod_hits if h.get("lod_level")})
        tail = "由 lod=true 的同槽扩张覆盖" if lod else "lod=false 时不改它"
        return "属 LOD 档（LOD" + ",".join(str(x) for x in levels) + "），" + tail
    return "真件 prefab 里没有同名渲染器节点（炮塔等属于别的 prefab）"


def _glb_mesh_node_names(glb: str | Path) -> list[str]:
    """glb 里带 mesh 的节点名（只读 JSON 块，不解几何）。"""
    doc, _blob = _glb_chunks(glb)
    out: list[str] = []
    for index, node in enumerate(doc.get("nodes") or []):
        if "mesh" in node:
            out.append(str(node.get("name") or f"node{index}"))
    return out


def _glb_mesh_node_parents(glb: str | Path) -> dict[str, str]:
    """glb 里「带 mesh 的节点名 -> 最近的**有名字的祖先**」（只读 JSON 块）。

    ★ 2026-10-30：新建节点要挂在哪个父级下 —— 这件事 Blender 那边已经决定了，
      所以从 glb 的 nodes[].children 反推，**别让用户再填一遍**。
    """
    doc, _blob = _glb_chunks(glb)
    nodes = doc.get("nodes") or []
    parent_of: dict[int, int] = {}
    for i, node in enumerate(nodes):
        for child in node.get("children") or []:
            parent_of[int(child)] = i
    out: dict[str, str] = {}
    for index, node in enumerate(nodes):
        if "mesh" not in node:
            continue
        name = str(node.get("name") or f"node{index}")
        cur = parent_of.get(index)
        guard = 0
        while cur is not None and guard < 64:
            pname = str(nodes[cur].get("name") or "")
            if pname:
                out[name] = pname
                break
            cur = parent_of.get(cur)
            guard += 1
    return out


def _glb_mesh_node_ancestors(glb: str | Path, depth: int = 3) -> dict[str, list[str]]:
    """glb 里「带 mesh 的节点名 -> 最近的几个**有名字的祖先**」（近 --> 远，最多 depth 个）。

    ★ 2026-10-30：用户把「新节点父级 / 材质来源」两个输入框删了（「不明所以」）——
      父级由上面那个函数反推；材质来源默认取**父级的父级**（例 rotor -> rot_blade_1 -> Chassis），
      因为父级常常是空物体、底下没有现成渲染器可抄材质。抄不到时 Unity 那边会退回父级并打 warning。
    """
    doc, _blob = _glb_chunks(glb)
    nodes = doc.get("nodes") or []
    parent_of: dict[int, int] = {}
    for i, node in enumerate(nodes):
        for child in node.get("children") or []:
            parent_of[int(child)] = i
    out: dict[str, list[str]] = {}
    for index, node in enumerate(nodes):
        if "mesh" not in node:
            continue
        name = str(node.get("name") or f"node{index}")
        chain: list[str] = []
        cur = parent_of.get(index)
        guard = 0
        while cur is not None and guard < 64 and len(chain) < depth:
            pname = str(nodes[cur].get("name") or "")
            if pname:
                chain.append(pname)
            cur = parent_of.get(cur)
            guard += 1
        out[name] = chain
    return out


def _emit_fbx_job(*, fbx, glb, asset_path, prefab_file, out, bundle_out, bundle_name,
                  report, asset_dir, proj, lod, wanted, lod0, lodn,
                  keywords=None, value_patch=None, shader_bind_patch=None,
                  create_missing=False, create_parent="", material_from="",
                  new_root="", material_source="") -> dict[str, Any]:
    """FBX 版 job：只给「哪一份 FBX + 换哪些渲染器」，网格由 Unity 导入器负责。"""
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    glb_parents = _glb_mesh_node_parents(glb) if create_missing else {}
    glb_ancestors = _glb_mesh_node_ancestors(glb) if create_missing else {}
    for name in _glb_mesh_node_names(glb):
        if wanted is not None and name not in wanted:
            skipped.append({"node": name, "reason": "未选中（--parts 里没有它）"})
            continue
        hits = lod0.get(name) or []
        if not hits and create_missing:
            # ★ 2026-10-30：真件 prefab 里没有这个节点 ⇒ 交给 Unity 侧新建（父级从 glb 反推）。
            parent = str(create_parent or glb_parents.get(name) or "")
            mat_from = str(material_from or "")
            if not mat_from:
                # 默认抄「父级的父级」：父级多半是空物体，底下没有现成渲染器。
                chain = glb_ancestors.get(name) or []
                mat_from = chain[1] if len(chain) > 1 else (chain[0] if chain else "")
            rows.append({"node": name, "lod": False, "path": "", "create": True,
                         "parent": parent,
                         # ★ 2026-10-30：**完整祖先链**（近→远）。父级本身往往也是空物体
                         #   （实测 `rot_blade_1` 的父级是 `Chassis`），只给 parent 会让 Unity
                         #   把它挂到根上 ⇒ 旋翼不跟车体动。C# 侧按这条链自根向下建。
                         "parent_chain": [str(a) for a in (glb_ancestors.get(name) or [])],
                         "material_from": mat_from})
            continue
        if len(hits) != 1:
            # ★ 2026-10-27：**同名多件、且引用同一份原网格 ⇒ 同一件，全部换同一份 FBX 网格**。
            #   现场：M1A2 SEP v3 的 `US_M2_Browning Unity 2k adaptation` 在 prefab 里有 5 个
            #   （TUSK1/TUSK2 各两件 + 主件），
            #   FBX 只有 1 份网格 ⇒ 原来判「按名字无法定位」跳过 ⇒ 这 5 个渲染器留着**包外的原网格**
            #   （`Assets/Mesh/Browning_body_0.asset`），
            #   Unity 打进包时把包外引用写成 fileID 0 ⇒ 游戏里 M2 机枪整条不显示
            #   （用户 2026-10-27 报的现象）。
            #   判据：这 5 件 `mesh_guid` 全相同（都是 549a7a98…）⇒ 不是「两件不同的东西重名」。
            #   ⛔ 只要 guid 不一致或为空，仍然跳过（不许瞎配）。
            guids = {str(h.get("mesh_guid") or "") for h in hits}
            if hits and len(guids) == 1 and "" not in guids:
                for hit in hits:
                    rows.append({"node": name, "lod": bool(lod),
                                 "path": str(hit.get("path") or "")})
                continue
            skipped.append({"node": name,
                            "reason": _skip_reason(name, hits, lodn.get(name) or [], lod)})
            continue
        rows.append({"node": name, "lod": bool(lod),
                     "path": str(hits[0].get("path") or "")})
    if not rows:
        detail = "; ".join(str(s.get("node")) + "（" + str(s.get("reason")) + "）"
                           for s in skipped)
        raise UnityJobError(
            "一件都没配上真件节点 —— 空的 parts 等于「不换网格」，不许当成功",
            hint="skipped：" + detail)
    job = {
        # ★ 2026-10-30：空串 = **从零制造**（C# 侧拿 root 新建 GameObject）。
        "prefab_path": asset_path,
        "root": str(new_root or ""),
        "fbx_path": str(Path(fbx).resolve()),
        "out_dir": bundle_out.resolve().as_posix(),
        "work_asset_dir": str(asset_dir),
        "bundle_name": str(bundle_name),
        "report": report.resolve().as_posix(),
        "parts": rows,
        "skipped": skipped,
        # ★ 2026-10-26：真件材质的着色器关键字（AssetRipper 把 valid/invalid 写反了，
        #   打包出来 _NORMALMAP/_MASKMAP 是关的 ⇒ 法线/遮罩图不起作用）。C# 侧照这份重写。
        "material_keywords": list(keywords or ()),
        # ★ 2026-10-26：真件材质的数值对齐读数（改了哪几颗 / 几处）。⛔ C# 侧不读它，纯留痕。
        "material_values": dict(value_patch or {}),
        # ★ 2026-10-27：材质 shader 绑定的读数（从 AssetRipper 桩指回工程真件几颗）。
        #   ⛔ C# 侧不读它，纯留痕 —— 但它决定包里那颗 `HDRP/Lit` 是 1 Pass 还是 8 Pass。
        "shader_bind": dict(shader_bind_patch or {}),
        # ★ 2026-11-01：从零造没有材质来源 ⇒ FBX 导入出来的是空材质（实测包内 2 颗、绑定贴图 0
        #   张，真机「白模」）。这里给「同单位真件 prefab」的**资产路径**，C# 侧照节点名抄材质槽。
        "material_source": str(material_source or ""),
    }
    # ★ 2026-10-29：动画配置就在工程目录里（`model.fbx` 旁边那个 `anim_config.json`）。
    #   约定：**有就带上，没有就完全不动 prefab 上真件的 AnimationHub**（保持继承行为）。
    _anim_cfg = Path(fbx).parent / "anim_config.json"
    if _anim_cfg.is_file():
        job["anim_config"] = _anim_cfg.resolve().as_posix()
    job_path = out / "job.json"
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "job": str(job_path.resolve()),
        "prefab": asset_path,
        "prefab_file": str(prefab_file or ""),
        "parts": rows,
        "skipped": skipped,
        "bytes": 0,
        "bundle_out": str(bundle_out),
        "report": str(report),
        "bundle_name": str(bundle_name),
        "lod": bool(lod),
        "material_keywords": len(keywords or ()),
        "unity": str(DEFAULT_UNITY_EXE),
        "project": str(proj),
        "mode": "fbx",
    }

def build_job(
    project_dir: str | Path,
    *,
    prefab_path: str,
    out_dir: str | Path,
    bundle_name: str = "unitbuild",
    parts: Sequence[str] | None = None,
    lod: bool = True,
    asset_dir: str = "Assets/_unitbuild",
    unity_project: str | Path | None = None,
    bundle_out_dir: str | Path | None = None,
    report_path: str | Path | None = None,
    # ★ 2026-10-30：真件 prefab 里没有的网格 ⇒ 新建节点（默认关，行为与以前逐字相同）
    create_missing: bool = False,
    create_parent: str = "",
    material_from: str = "",
    # ★ 2026-10-30：**制造**的唯一入口。给了 new_root 又不给 prefab_path ⇒ 从零造一个 prefab
    #   （根 = new_root，节点全由 FBX 建）；给了 prefab_path ⇒ 在那个 prefab 上造（= 改装）。
    #   ⛔ 两者都没有 ⇒ 仍然按老样抛（不猜）。
    new_root: str = "",
    # ★ 2026-11-01：从零造时的**材质来源**（同单位真件 prefab 的资产路径）。空 = 不抄（会白模）。
    material_source: str = "",
) -> dict[str, Any]:
    """工程（含 `model.fbx`）-> `job.json`（规格 §2）。**不启 Unity**。

    唯一一条路：`model.fbx` 交给 Unity 的 `ModelImporter` 造 Mesh 资产 ——
    Python 侧**一个网格字节都不写**（手写网格那条旧路 2026-10-25 整体退役）。

    返回 ``{"job", "parts", "skipped", "bytes", "mode": "fbx", ...}``：
      · `parts`   = 真进了 job 的那些件（每件带 node / lod / path）；
      · `skipped` = 配不上真件节点的件 + **逐条原因**（⛔ 不静默丢）；
      · `bytes`   = Python 写盘的网格字节数（FBX 模式恒 0：网格由 Unity 造）。
    """
    proj = Path(project_dir)
    if not proj.is_dir():
        raise UnityJobError(f"工程目录不存在：{proj}")
    # ★ 2026-10-29：`.blend` 比 `model.fbx` 新（或 fbx/glb 缺）⇒ **自动重导**，
    #   用户改完 .blend 不用再手工出 fbx / anim_config.json。起不来就抛（⛔ 不许用旧 fbx）。
    from ba_mod_maker.core.assets import anim_pack as _anim_pack

    _auto = _anim_pack.auto_export_from_blend(proj)
    if _auto is not None:
        print(f"ANIMEXPORT {_auto['reason']} -> model.fbx/glb + anim_config.json"
              f"（behaviours={_auto['behaviours']}）")
    fbx = proj / "model.fbx"
    if not fbx.is_file():
        raise UnityJobError(
            f"工程目录里没有 model.fbx：{proj}",
            hint="网格只能由 Unity 的 ModelImporter 导入 —— 要 model.fbx；glb / OBJ 不行"
                 "（Python 手写网格那条旧路已退役）。用断箭模型提取器再导一次，"
                 "它默认出 model.glb + model.fbx")
    glb = proj / "model.glb"
    if not glb.is_file():
        raise UnityJobError(
            f"工程目录里没有 model.glb：{proj}",
            hint="FBX 那条路还要 glb 的节点名去对真件 prefab 的渲染器 —— "
                 "用断箭模型提取器再导一次（它默认出 model.glb + model.fbx）")
    unity = unity_project_path(unity_project)
    # ★ 2026-10-30：**制造** vs **改装** —— 同一入口，区别只在「有没有底包」。
    #   · 有 prefab_path ⇒ 读它、在它上面换网格（原来的行为，一字不改）；
    #   · 没有 prefab_path 但有 new_root ⇒ 从零造：根 = new_root，节点全由 FBX 建；
    #   · 两个都没有 ⇒ 抛（⛔ 不猜）。
    from_zero = False
    if str(prefab_path or "").strip():
        asset_path, prefab_file = _resolve_prefab(prefab_path, unity)
    elif str(new_root or "").strip():
        from_zero = True
        asset_path, prefab_file = "", None
        create_missing = True
    else:
        raise UnityJobError(
            "既没给底包 prefab、也没给新 prefab 根名 —— 不知道要造什么",
            hint="从零制造：--new-root <根节点名>；改装：--prefab <导出工程里已有的 prefab>")
    # ★ 2026-10-28：**起 Unity 之前**判「AnimationHub 的 [SerializeReference] 行为类有没有桩」。
    #   缺桩时 Unity 把那一项写成 null（包里 rid=-2）⇒ AnimationHub.Init 抛 NRE；
    #   旧链路要等 Unity 跑完、包出来才由 A10 报红，那时包里已丢类名、说不清缺哪个类。
    #   放在这一层（unity_prefab.build_job）而不是某个外层入口 —— CLI / 导入器 / 管理器都走它。
    # ★ 2026-10-30：从零制造时**没有底包可查** —— 行为类桩只能等 Unity 跑完再报（读数留痕）。
    if prefab_file is None:
        stub_total = -1
        prefab = {"renderers": []}
    else:
        stub_total = behaviour_stubs.assert_stubs_complete(
            prefab_file, stub_root=Path(unity) / behaviour_stubs.STUB_REL)
        prefab = read_prefab(prefab_file)
        if not prefab["renderers"]:
            raise UnityJobError(f"{prefab_file.name} 里一个渲染器都没有")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bundle_out = Path(bundle_out_dir) if bundle_out_dir else DEFAULT_BUNDLE_OUT
    report = Path(report_path) if report_path else (out / "report.json")

    glb_names = _glb_mesh_node_names(glb)
    wanted = None if not parts else [str(p).strip() for p in parts if str(p).strip()]
    if wanted is not None:
        missing = [w for w in wanted if w not in glb_names]
        if missing:
            raise UnityJobError(
                "工程 glb 里没有这些节点：" + "、".join(missing),
                hint="glb 里的网格节点：" + "、".join(glb_names))

    lod0: dict[str, list[dict[str, Any]]] = {}
    lodn: dict[str, list[dict[str, Any]]] = {}
    for node in prefab["renderers"]:
        bucket = lod0 if node.get("lod_level") in (None, 0) else lodn
        bucket.setdefault(str(node["name"]), []).append(node)

    # ★ 2026-10-26：AssetRipper 的桩 shader 没有 `#pragma shader_feature` ⇒ 材质里的
    #   `_NORMALMAP`/`_MASKMAP` 被判成「不合法」⇒ 打进包里就是**关**的（法线/遮罩不显示）。
    #   先把真件用到的关键字声明进桩 shader，Unity 才会把它们当合法关键字写回材质。
    keywords = material_keywords.keywords_of(proj)
    patch = material_keywords.ensure_shader_keywords(unity, keywords) if keywords else {}
    # ★ 2026-10-26：AssetRipper 导出的材质**数值**也会错（实测 Kord 的 `_MetallicRemapMax` 写成 0、
    #   真件 1.0 ⇒ 带 `_MASKMAP` 时 metallic≡0 ⇒ 整把枪当漆面渲染：亮一档、更平、没金属感）。
    #   按真件 `prefab_graph.json` 的 `m_SavedProperties` 对齐 `.mat` 的 floats/colors（幂等）。
    values = material_values.values_of(proj)
    value_patch = (material_values.align_materials(unity, prefab_file, values)
                   if (values and prefab_file is not None) else {})
    # ★ 2026-10-27：AssetRipper 给每个真 shader 导出一份**同名桩**放 `Assets/Shader/`
    #   （1 个 Pass），材质默认全挂桩 ⇒ 真机「在、但平」（M1A2 SEP v3 的 M2/M240L 实测）。
    #   造包前把「挂在桩上、工程里有同名真件」的材质改指真件（8 个 Pass）；幂等。
    #   范围 = **整个工程**（不只这个 prefab 引到的）：AH-1Z 实测有 1 颗材质是从依赖件
    #   （Destroyed_*）带进包的，按 prefab 卡范围会漏 ⇒ 包里仍有一颗 1 Pass 桩。
    #   桩→真件是恒等式（只把 `m_Shader` 那一行的 guid 换掉），全工程扫是安全的。
    shader_patch = shader_bind.bind_real_shaders(unity)
    res = _emit_fbx_job(
        fbx=fbx, glb=glb, asset_path=asset_path, prefab_file=prefab_file,
        out=out, bundle_out=bundle_out, bundle_name=bundle_name, report=report,
        asset_dir=asset_dir, proj=proj, lod=bool(lod), wanted=wanted,
        lod0=lod0, lodn=lodn,
        keywords=keywords, value_patch=value_patch, shader_bind_patch=shader_patch,
        create_missing=bool(create_missing), create_parent=str(create_parent or ""),
        material_from=str(material_from or ""),
        new_root=str(new_root or ""),
        material_source=str(material_source or ""),
    )
    res["shader_keywords"] = patch
    res["material_values"] = value_patch
    res["shader_bind"] = shader_patch
    # ★ 2026-10-28：行为类桩覆盖读数（缺桩时上游已抛错，走到这里就是全有桩）。
    res["stub_check"] = f'行为类桩覆盖 {stub_total}/{stub_total}'
    return res
