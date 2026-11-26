r"""L12 · `AnimationManager._container` **整表导出**（只读，⛔ 一个字节都不写）。

## 为什么需要（用户 2026-10-20 定）

用户口径：「我希望 Blender 里的动画，就是游戏中能动的动画」。游戏里载具的"动"分三层：
① Animator 的 clip（只有舱盖）· ② `AnimationManager._container` 的参数表（轮速/履带/悬挂）·
③ `AnimationHub` 的状态机。

已有入口 `anim params` **只读 `_container._settings` 那 7 个 float**（`cli/anim.py:32` 明写
「`_suspension` / `_acceleration` / `_recoils` 一个都不碰」）。本模块把**整张表**导出来，
并把里面的 PPtr 解析成**骨骼名 / 路径**——那是复刻驱动效果的原料。

## 三条口径

1. **只读**：`open_source` 打开、`read_typetree` 读、关掉，不碰写面（没有 `save_typetree`）；
2. **PPtr 要解成人能读的东西**：`{m_FileID, m_PathID}` -> `{path_id, name}`，解不出就**原样保留**
   并记 `"_unresolved": true`（⛔ 不丢字段、不猜）；
3. **不是 `AnimationManager` 就报红**：树里没有 `_container` ⇒ `AssetError`
   （⛔ 不返回空表冒充成功）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import unitypy_source
from ba_mod_maker.core.errors import AssetError

__all__ = ["read_container", "is_pptr"]

#: PPtr 的字段形状（Unity 序列化里就是这个 `{m_FileID, m_PathID}`）。
PPTR_KEYS = ("m_FileID", "m_PathID")


def is_pptr(value: Any) -> bool:
    """这个值看起来是不是一条 PPtr（**两个键都在**才算）。"""
    if not isinstance(value, dict):
        return False
    return all(key in value for key in PPTR_KEYS)


def _name_of(source: Any, path_id: int) -> str:
    """PPtr 指向的那个对象叫什么（解不出返回空串，⛔ 不抛）。

    ★ 2026-10-20：`_suspension` / `_wheels` / `_traks` 里那批 PPtr 大多指着 **Transform 组件**，
    而 Transform **没有 `m_Name`**（名字在 `m_GameObject` 上）—— 只读一层会全军 `_unresolved`
    （实测 12 个悬挂元素 + 轮子全解不出）。所以要**两跳**：Transform -> m_GameObject -> m_Name。
    """
    try:
        tree = source.object_at(int(path_id)).read_typetree()
    except Exception:  # noqa: BLE001 -- 跨包/解不出都算解不出
        return ""
    if not isinstance(tree, dict):
        return ""
    name = str(tree.get("m_Name") or tree.get("name") or "")
    if name:
        return name
    owner = tree.get("m_GameObject")
    if isinstance(owner, dict) and "m_PathID" in owner:
        try:
            owner_tree = source.object_at(int(owner["m_PathID"])).read_typetree()
        except Exception:  # noqa: BLE001
            return ""
        if isinstance(owner_tree, dict):
            return str(owner_tree.get("m_Name") or "")
    return ""


def _resolve(source: Any, value: Any, *, depth: int = 0) -> Any:
    """把树里的 PPtr 换成 `{path_id, name}`；其余原样（递归）。"""
    if depth > 8:
        return value
    if is_pptr(value):
        path_id = int(value.get("m_PathID") or 0)
        if path_id == 0:
            return {"path_id": 0, "name": "", "null": True}
        out: dict[str, Any] = {"path_id": path_id, "file_id": int(value.get("m_FileID") or 0)}
        name = _name_of(source, path_id)
        if name:
            out["name"] = name
        else:
            out["_unresolved"] = True
        return out
    if isinstance(value, dict):
        return {k: _resolve(source, v, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_resolve(source, v, depth=depth + 1) for v in value]
    return value


def read_container(bundle: str | Path, path_id: int) -> dict[str, Any]:
    """读一颗 `AnimationManager` 的 `_container` **整表**（PPtr 已解析）。

    :raises AssetError: 对象不在 / 不是 AnimationManager（树里没有 `_container`）。
    """
    target = Path(bundle)
    with unitypy_source.open_source(target) as source:
        try:
            tree = source.object_at(int(path_id)).read_typetree()
        except Exception as exc:
            raise AssetError(
                f"包里读不到 pathID {path_id}",
                hint=f"确认这个 pathID 在那个包里（{exc}）",
            ) from exc
        if not isinstance(tree, dict) or "_container" not in tree:
            raise AssetError(
                f"pathID {path_id} 不是 AnimationManager（树里没有 _container）",
                hint="pathID 要取**挂在单位 prefab 上那颗** AnimationManager",
            )
        container = _resolve(source, tree["_container"])
    settings = (container or {}).get("_settings") or {}
    suspension = (container or {}).get("_suspension") or {}
    elements = suspension.get("Elements") or []
    return {
        "bundle": str(target),
        "path_id": int(path_id),
        "family": "AnimationManager._container",
        "settings": settings,
        "suspension_elements": len(elements),
        "container": container,
    }