r"""资产浏览器内核：读一个 UnityFS 包列出资产 ＋ 往 catalog 批量登记地址（纯逻辑，不写盘）。

这一层是给「断箭资产管理器」用的 core API（⛔ 不含 GUI、⛔ 不碰游戏目录）。四个公开入口：

* list_bundle_contents —— 读一个 .bundle，逐对象给出
  (asset_name, asset_path, path_id, class_id, type_class, asset_path_source)；
* suggest_key / suggest_asset_path —— 纯函数的建议口径（用户可覆盖）；
* register_batch —— 把一批 (key, asset_path, bundle) 登记进二进制 catalog，
  已登记的键跳过（正控：同一批跑两遍，第二遍 0 条新增）。

## 读法只有一份（⛔ 不在这里重写 UnityFS 解析）

包内对象表 / 容器条目复用 core/assets/unit_pack.py::_open_pack 那一份读法
（背后是 core/asset/ufs.py ＋ asset_kernel/metadata_writer ＋ asset_kernel/clone）。
本模块只做面向资产浏览的编排：容器条目 → 真实资产路径、对象名 → 建议键/路径。

为什么必须碰那个私有入口：unit_pack.list_objects 只有 pathID/名字，拿不到容器条目；
unit_pack.list_assets 只有路径，拿不到它对应的 pathID 与对象名。本文件的写入范围只允许它自己，
所以在唯一需要的地方显式 `# noqa: SLF001`，理由写在同一行。

## 哪些对象读不出名字（不是 bug，别当失败）

发布包里 classID 43/28/48/115（Mesh / Texture2D / Shader / MonoScript）没有类型树
（与 unit_pack._LEAF_CLASSES 同口径）⇒ m_Name 读不出来 ⇒ asset_name == ""、
asset_path_source == "none"。真包实测（_work/unitybuild/out-ah1z3/*.bundle）：
643 个对象 / 341 个有名字 / 1 条容器条目。

## 判据一行

    .venv\Scripts\python.exe -m pytest tests/test_asset_browser.py -q -n 0
      -> 真包 643 个对象、prefab 名 US_AH-1Z Viper；同一批登记跑两遍，第二遍 added == 0

## ⛔ 没验过的（别当成验过了）

1. 真机没验：登记出来的地址能不能被游戏解析（要用户起游戏）。
2. 没有 GUI：本模块只给 API；界面的取舍不在这一层。
3. 只读了「有类型树的类」的名字；别的类的名字口径没有独立对拍件。
"""

from __future__ import annotations

import re
import struct
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import clone
from ba_mod_maker.core.assets import catalog_bin as cb
from ba_mod_maker.core.assets import catalog_bin_write as cbw
from ba_mod_maker.core.assets import unit_pack
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "CLASS_NAMES",
    "KEY_MAX_LENGTH",
    "MODEL_ASSET_PREFIX",
    "NO_TYPE_TREE_CLASSES",
    "RESULT_STATUSES",
    "ROW_KEYS",
    "list_bundle_contents",
    "register_batch",
    "suggest_asset_path",
    "suggest_key",
]

#: 模型地址的资产路径前缀（真件形状；与 core/asset/new_asset.py::MODEL_ASSET_PREFIX 同口径，
#: 那边是「产品自己派生命名」，这边是**给用户看的建议值** —— 用户可覆盖）。
MODEL_ASSET_PREFIX = "Assets/Resources_moved/ModelPrefabs/"

#: 建议键的长度上限（core/asset/new_asset.py::KEY_PATTERN 是 1 + 63 = 64 字符）。
KEY_MAX_LENGTH = 64

#: 读不出名字的叶子对象：类型树不在包里（与 unit_pack._LEAF_CLASSES 同口径）。
NO_TYPE_TREE_CLASSES = frozenset({28, 43, 48, 115})

#: list_bundle_contents 每行的键（GUI 与用例都按这六个键收口）。
ROW_KEYS = (
    "asset_name",
    "asset_path",
    "path_id",
    "class_id",
    "type_class",
    "asset_path_source",
)

#: register_batch 逐条结果的 status 取值。
RESULT_STATUSES = ("added", "skipped", "failed")

#: classID -> Unity 类名（只用于显示；未知一律 "class<N>"）。
#: 为什么不复用 clone.CLASS_NAMES：那份只覆盖 4 类（GameObject/Transform/MeshRenderer/
#: MeshFilter），是给读数用的最小集；资产浏览器要覆盖包里常见的类，未知仍有兜底。
CLASS_NAMES: Mapping[int, str] = {
    1: "GameObject",
    4: "Transform",
    21: "Material",
    23: "MeshRenderer",
    28: "Texture2D",
    33: "MeshFilter",
    43: "Mesh",
    48: "Shader",
    74: "AnimationClip",
    90: "Avatar",
    91: "AnimatorController",
    95: "Animator",
    114: "MonoBehaviour",
    115: "MonoScript",
    137: "SkinnedMeshRenderer",
    142: "AssetBundle",
    205: "LODGroup",
    213: "Sprite",
    224: "RectTransform",
}


def _class_name(class_id: int) -> str:
    """classID -> 类名（未知给 "class<N>"，⛔ 不猜）。"""
    return CLASS_NAMES.get(int(class_id), f"class{int(class_id)}")


def _object_name(pack: Any, spec: Any) -> str:
    """读一个对象的 m_Name；类型树不在包里（叶子类）⇒ 空串。

    失败只吞 AssetError（= 这个类的类型树读不出来）；别的异常照抛 —— 那是真 bug，
    不该被「名字读不出来」盖住。
    """
    if int(spec.class_id) in NO_TYPE_TREE_CLASSES:
        return ""
    try:
        type_id = unit_pack._type_id_of(pack, spec)  # noqa: SLF001 —— 复用唯一一份类型表定位
        nodes = pack.nodes_for(type_id)
        fields = clone.read_object(spec.payload, nodes, ref_types=pack.pool)
    except AssetError:
        return ""
    if isinstance(fields, Mapping):
        return str(fields.get("m_Name") or "")
    return ""


def list_bundle_contents(bundle_path: str | Path) -> list[dict[str, Any]]:
    """读一个 UnityFS 包，逐对象列出资产（**只读**，⛔ 不写盘）。

    每行的键固定六个（ROW_KEYS）：

    * asset_name —— 包内真实对象名（m_Name）；叶子类读不出来 ⇒ ""；
    * asset_path —— 该 pathID 是**容器条目**时给包内真实容器路径（Unity 存成小写），
      否则给 suggest_asset_path(asset_name)；asset_name 为空 ⇒ ""；
    * path_id / class_id / type_class —— 对象身份（type_class 未知给 class<N>）；
    * asset_path_source —— "container" / "suggested" / "none"，说明 asset_path 的来源。

    排序 = path_id 升序。空包 / 坏包 / 文件不存在 ⇒ AssetError（fail-closed）。
    """
    path = Path(bundle_path)
    if not path.is_file():
        raise AssetError(
            f"包不存在：{path}",
            hint="核对路径；本函数只读，不去游戏目录里猜",
        )
    # 理由：唯一能同时拿到容器 pathID 与对象表的入口就是它（见模块头）。
    pack = unit_pack._open_pack(path)  # noqa: SLF001
    try:
        specs = sorted(pack.specs, key=lambda spec: int(spec.path_id))
        if not specs:
            raise AssetError(
                f"包里没有对象：{path.name}",
                hint="空包 / 坏包；先用产品的 pack check-model 或重造一次，别拿它当资产浏览结果",
            )
        container: dict[int, str] = {}
        for asset_path, path_id in pack.container:
            container.setdefault(int(path_id), str(asset_path))
        rows: list[dict[str, Any]] = []
        for spec in specs:
            class_id = int(spec.class_id)
            path_id = int(spec.path_id)
            name = _object_name(pack, spec)
            if path_id in container:
                asset_path = container[path_id]
                source = "container"
            elif name:
                asset_path = suggest_asset_path(name)
                source = "suggested"
            else:
                asset_path = ""
                source = "none"
            rows.append(
                {
                    "asset_name": name,
                    "asset_path": asset_path,
                    "path_id": path_id,
                    "class_id": class_id,
                    "type_class": _class_name(class_id),
                    "asset_path_source": source,
                }
            )
        return rows
    finally:
        pack.raw_reader.close()


def suggest_key(asset_name: str) -> str:
    """由包内对象名派生一个**可能合法**的 catalog 键（纯函数，用户可覆盖）。

    口径：大写 → 非 [A-Za-z0-9._-] 变 "_" → 合并连续 "_" → 去首尾 "_" / "." / "-" →
    截断 64 → 再去首尾。
    例："US_AH-1Z Viper" -> "US_AH-1Z_VIPER"。空白入参 / 派生不出 ⇒ AssetError。
    """
    text = str(asset_name or "").strip()
    if not text:
        raise AssetError(
            "资产名是空的 —— 建议键要从包内对象名派生",
            hint="先 list_bundle_contents 拿到 asset_name；读不出名字的叶子类需要用户手填键",
        )
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", text).upper()
    slug = re.sub(r"_{2,}", "_", slug).strip("._-")
    slug = slug[:KEY_MAX_LENGTH].strip("._-")
    if not slug or not slug[0].isalnum():
        raise AssetError(
            f"这个名字派生不出合法键：{asset_name!r}",
            hint="键要以字母数字开头、只含字母数字与 . _ -（例 MY_MODEL_01）",
        )
    return slug


def suggest_asset_path(prefab_name: str) -> str:
    """由 prefab 名派生**建议的**资产路径（纯函数，用户可覆盖）。

    形状 = Assets/Resources_moved/ModelPrefabs/<名>.prefab（真件形状）。
    已经以 .prefab 结尾就不重复加；空白入参 ⇒ AssetError。
    """
    text = str(prefab_name or "").strip().replace("\\", "/").strip("/")
    if not text:
        raise AssetError(
            "prefab 名是空的 —— 假资产路径要从名字派生",
            hint="先 list_bundle_contents 拿到 asset_name；读不出名字时需要用户手填路径",
        )
    if text.lower().endswith(".prefab"):
        text = text[: -len(".prefab")]
    text = text.strip("/")
    if not text:
        raise AssetError(
            f"这个名字派生不出资产路径：{prefab_name!r}",
            hint="只要名字，不要只给一个 .prefab 后缀",
        )
    return f"{MODEL_ASSET_PREFIX}{text}.prefab"


def _entry_fields(raw: object, default_resource_type: int) -> tuple[str, str, str, int, str]:
    """拆一条 entry：返回 (key, asset_path, bundle, resource_type, problem)。

    problem 非空 = 这条 entry 不合法（⛔ 不静默修它，原样报给调用方）。
    """
    if not isinstance(raw, Mapping):
        return "", "", "", int(default_resource_type), f"bad_entry:不是字典（{type(raw).__name__}）"
    key = str(raw.get("key") or "").strip()
    asset_path = str(raw.get("asset_path") or "").strip()
    bundle = str(raw.get("bundle") or "").strip()
    rtype_raw = raw.get("resource_type", default_resource_type)
    try:
        rtype = int(rtype_raw)
    except (TypeError, ValueError):
        return key, asset_path, bundle, int(default_resource_type), (
            f"bad_entry:resource_type 不是整数（{rtype_raw!r}）"
        )
    missing = [
        name
        for name, value in (("key", key), ("asset_path", asset_path), ("bundle", bundle))
        if not value
    ]
    if missing:
        return key, asset_path, bundle, rtype, "bad_entry:缺 " + "/".join(missing)
    return key, asset_path, bundle, rtype, ""


def _result(key: str, asset_path: str, bundle: str, status: str, reason: str) -> dict[str, str]:
    """一条登记结果的**固定五键**（GUI 按它收口）。"""
    return {
        "key": key,
        "asset_path": asset_path,
        "bundle": bundle,
        "status": status,
        "reason": reason,
    }


def register_batch(
    catalog_bytes: bytes,
    entries: Sequence[Mapping[str, Any]],
    *,
    source_note: str = "",
    resource_type: int = 1,
) -> dict[str, Any]:
    """把一批地址登记进**二进制 catalog**（纯函数：字节进、字节出，⛔ 不碰盘）。

    entries 每条：必填 key / asset_path / bundle（bundle = catalog 里那个**包条目的键名**，
    即 xxx_assets_all_<16hex>.bundle）；可选 resource_type（默认取函数参数；1 = prefab，3 = 图标）。

    行为（三条 fail-closed 口径）：

    1. 已登记的键**跳过**（status "skipped"、reason "key_exists"）；同批里重复的键同样跳过；
    2. 包没登记进 catalog ⇒ 该条 failed + reason "bundle_not_registered"（⛔ 不静默跳过，
       也不替它登记包 —— 包由 newasset 那条链登记）；
    3. 条目本身缺字段 / 不是字典 ⇒ 该条 failed + reason "bad_entry:…"。

    依赖声明固定 = add_address(dependency_policy="own-only")（只依赖自己那个包条目）。
    source_note 只进返回的 lines（catalog 里没有备注字段，⛔ 不会写进字节）。

    返回 dict（键固定，逐字）：catalog_bytes / results / added / skipped / failed /
    changed / counts_before / counts_after / lines。
    """
    if not isinstance(catalog_bytes, (bytes, bytearray, memoryview)):
        raise AssetError(
            f"catalog_bytes 要是 bytes，不是 {type(catalog_bytes).__name__}",
            hint="先读盘拿到 aa\\catalog.bin 的字节再传进来；本函数不碰盘",
        )
    before = bytes(catalog_bytes)
    if not cb.looks_like_catalog_bin(before):
        raise AssetError(
            "这不是二进制 catalog.bin（magic 对不上）",
            hint="1.2.0 及以前是 catalog.json 明文；这条链只吃 1.2.1 的 catalog.bin（见 kb/40）",
        )
    try:
        model = cbw.read_model(before)
    except (ValueError, struct.error, AssetError) as exc:
        raise AssetError(
            f"这份 catalog.bin 读不动：{exc}",
            hint="截断 / 版本不符；换一份完整的 catalog.bin，别拿半截字节试",
        ) from exc
    counts_before = (len(model.keys), len(model.keys), len(model.entries))
    known = {str(key.name) for key in model.keys}
    note = str(source_note or "").strip()
    lines = [f"批量登记：{len(entries)} 条" + (f"（{note}）" if note else "")]
    results: list[dict[str, str]] = []
    current = before
    added = skipped = failed = 0
    for raw in entries:
        key, asset_path, bundle, rtype, problem = _entry_fields(raw, resource_type)
        if problem:
            failed += 1
            results.append(_result(key, asset_path, bundle, "failed", problem))
            lines.append(f"  ! {key or '（无键）'}：{problem}")
            continue
        if key in known:
            skipped += 1
            results.append(_result(key, asset_path, bundle, "skipped", "key_exists"))
            lines.append(f"  = {key}：已登记，跳过")
            continue
        if bundle not in known:
            failed += 1
            results.append(_result(key, asset_path, bundle, "failed", "bundle_not_registered"))
            lines.append(f"  ! {key}：包 {bundle} 没在 catalog 里")
            continue
        try:
            plan = cbw.add_address(
                current,
                key=key,
                asset_path=asset_path,
                bundle_name=bundle,
                resource_type=rtype,
                dependency_policy="own-only",
            )
        except AssetError as exc:
            failed += 1
            results.append(_result(key, asset_path, bundle, "failed", f"add_address:{exc}"))
            lines.append(f"  ! {key}：add_address 拒绝 —— {exc}")
            continue
        current = plan.after
        known.add(key)
        added += 1
        results.append(_result(key, asset_path, bundle, "added", ""))
        lines.append(f"  + {key} -> {asset_path}（包 {bundle} · resource_type {rtype}）")
    counts_after = cbw.counts(current) if added else counts_before
    lines.append(
        f"合计：新增 {added} · 跳过 {skipped} · 失败 {failed}"
        f"（条目 {counts_before[2]} -> {counts_after[2]}）"
    )
    return {
        "catalog_bytes": current,
        "results": results,
        "added": added,
        "skipped": skipped,
        "failed": failed,
        "changed": current != before,
        "counts_before": counts_before,
        "counts_after": counts_after,
        "lines": tuple(lines),
    }

