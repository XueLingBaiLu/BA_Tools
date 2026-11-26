"""资产 Δ 计划的落点与序列化（零 Qt，纯 stdlib + core）。

## 为什么单独一个模块

ADR 0008 决定 4：`save()` **一次冲出两条产物** —— 表改动 → **包**（ADR 0005 的主通道）；
资产改动 → **Δ 计划**。计划的**纯数据**形态是
`core/edit/asset_changeset.py::AssetPlan`（没有字节、没有 IO）；「它长什么样、落在哪」
放在这里，是为了让 `app/shell/edit_session.py` 只依赖 core —— 而不是让它去 import
`asset_kernel.reverse_delta`：那会让读路径反向依赖写路径
（`tests/test_core_layering.py` 用 AST 扫这条纪律）。

## 落在哪

    <工程目录>/patches/asset-plan.json

目录名 `patches` 与 `asset_kernel/reverse_delta.py::PATCH_DIR_NAME` 是**同一个**
（工程目录口径只有一处）。⛔ 文件名**不许**叫 `install.rmod.json` —— 那是 `PatchStore`
的**反向 Δ** 账本（一次安装一条、还原成功即删）；计划是**正向的、还没执行的**东西，
混进那个文件会把还原账本弄坏。

## 计划不是补丁

它只说「打算改哪几处、旧值是什么」。真正写字节、算反向 Δ、成对更新 catalog CRC 的是
`asset_kernel` 那一步（`writer.write_objects` / `plan_delta` / `plan_removals`），
它自带补丁柜。所以本模块**只写一份 JSON**，⛔ 不碰游戏目录、不建补丁、不改任何包。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ba_mod_maker.core.edit.asset_changeset import MISSING, AssetEdit, AssetPlan
from ba_mod_maker.core.edit.asset_refs import ByteSpan, FieldPath
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "PLAN_DIR_NAME",
    "PLAN_FILE_NAME",
    "load_plan_document",
    "plan_document",
    "plan_path",
    "write_plan",
]

#: Δ 计划落在工程目录下的哪个子目录。与 `reverse_delta.PATCH_DIR_NAME` 同值 —— 改一处要改两处。
PLAN_DIR_NAME = "patches"

#: Δ 计划的固定文件名（⛔ 与补丁柜的 `install.rmod.json` 分开，见模块头）。
PLAN_FILE_NAME = "asset-plan.json"

#: 文档版本。读的人（下个会话 / 用例）靠它认形状；形状变了就 +1。
SCHEMA_VERSION = 1


def plan_path(project_root: str | Path) -> Path:
    """Δ 计划的落点：`<工程目录>/patches/asset-plan.json`。"""
    return Path(project_root) / PLAN_DIR_NAME / PLAN_FILE_NAME


def _field_document(field: FieldPath | ByteSpan | None) -> dict[str, Any] | None:
    """字段那一格：**两级各自命名**（ADR 0008 决定 5）—— 混成一级就判不出「改回原值」。"""
    if field is None:
        return None
    if isinstance(field, FieldPath):
        return {"kind": "path", "path": field.path}
    return {"kind": "bytes", "offset": field.offset, "size": field.size}


def _value_document(value: Any) -> Any:
    """旧值 / 新值进 JSON 时的写法。

    `bytes` 只留长度 ＋ sha256 前 16 位：一份载荷可以到 500 KB，塞进计划文件既读不动
    也没人看（要字节的是 `asset_kernel` 那一步，不是这份计划）。
    """
    if value is MISSING:
        return {"missing": True}
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        return {
            "bytes": len(data),
            "sha256_16": hashlib.sha256(data).hexdigest()[:16],
        }
    return value


def _edit_document(edit: AssetEdit) -> dict[str, Any]:
    return {
        "kind": edit.kind,
        "container": str(edit.ref.container),
        "path_id": edit.ref.path_id,
        "field": _field_document(edit.ref.field),
        "old": _value_document(edit.old),
        "new": _value_document(edit.new),
        "summary": edit.summary,
    }


def plan_document(plan: AssetPlan) -> dict[str, Any]:
    """`AssetPlan` -> 一份可直接 `json.dumps` 的文档（**纯函数**，不碰盘）。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "asset-plan",
        "count": plan.count,
        "containers": [str(path) for path in plan.containers()],
        "edits": [_edit_document(edit) for edit in plan.edits()],
    }


def write_plan(plan: AssetPlan, *, project_root: str | Path) -> Path:
    """把 Δ 计划写进 `<工程目录>/patches/asset-plan.json`（临时文件 + 原子替换）。

    空计划 ⇒ `AssetError`：写一份「什么都没有」的计划文件，会让下一个人以为盘上有改动。
    """
    if plan.is_empty:
        raise AssetError(
            "没有资产改动可写：Δ 计划不是一份空文件",
            hint="先往账本里记一条（set_field / add_object / delete_object）再保存",
        )
    target = plan_path(project_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(plan_document(plan), ensure_ascii=False, indent=2, default=str) + "\n"
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
    return target


def load_plan_document(path: str | Path) -> dict[str, Any]:
    """读回一份计划（**写后复核**与用例用；形状不对就抛 `AssetError`，⛔ 不猜）。"""
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AssetError(
            f"读不出 Δ 计划 {source}：{exc}",
            hint="它应该是 write_plan() 写出来的那份 JSON",
        ) from exc
    if not isinstance(document, dict) or document.get("kind") != "asset-plan":
        raise AssetError(
            f"{source} 不是一份资产 Δ 计划（kind={document!r}）",
            hint="补丁柜的 install.rmod.json 是另一个文件，别读混了",
        )
    return document
