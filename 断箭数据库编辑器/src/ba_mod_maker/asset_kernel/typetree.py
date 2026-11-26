"""把「资产对象」统一成 typetree 映射（读侧的小适配件）。

两种输入都认：

1. 已经解好的 typetree（Mapping）—— 产品 venv 的用例、离线夹具走这条；
2. 还带着 read_typetree() 的对象（UnityPy 的 Container）—— Blender 侧走这条。

放在这里而不是各模块自己写一遍，是为了让 asset_kernel 里**只有一份**「怎么拿 typetree」。
本模块零第三方依赖（不 import UnityPy：第 2 条只要求对方**有**这个方法）。
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["as_typetree", "path_id_of"]


def as_typetree(obj: object) -> Mapping[str, object]:
    """取 typetree。

    :raises TypeError: 既不是 Mapping 也没有 read_typetree() —— 这时候**必须**抛，
        否则下一句会在一个错对象上取字段，得到的是一串 None 组成的「空资产」。
    """
    if isinstance(obj, Mapping):
        return obj
    reader = getattr(obj, "read_typetree", None)
    if callable(reader):
        tree = reader()
        if isinstance(tree, Mapping):
            return tree
        raise TypeError(f"read_typetree() 没给出映射：{type(tree).__name__}")
    raise TypeError(f"这不是一个资产对象（没有 read_typetree）：{type(obj).__name__}")


def path_id_of(obj: object, default: int = -1) -> int:
    """尽量取 pathID（UnityPy 的 Container 有 .path_id；typetree 里没有）。"""
    pid = getattr(obj, "path_id", None)
    if pid is None:
        reader = getattr(obj, "object_reader", None)
        pid = getattr(reader, "path_id", None)
    if pid is None:
        return default
    return int(pid)
