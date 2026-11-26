"""UnityFS 容器的**读侧**入口（薄封装，零新增逻辑）。

实现仍在产品既有的 ba_mod_maker.core.asset.ufs（W1 已交付）。本模块只做两件事：

1. 给下一单（W3 只读导入）一个**稳定名字**——open_bundle / read_serialized / read_object；
2. 把「写侧」挡在外面：设计稿 §9.10.4 的「搬到尾部」与 W1 落地的「分段重压」是同一
   目标的两种实现，W1 实测后者的收益已经抹平前者（111,982 块 -> 68 块）。写面规划器
   的口径留给 W5 裁定，所以这里**不**转出 rewrite_container / Replacement ——
   要用写侧就直接 import ba_mod_maker.core.asset.ufs，别从这里绕。

⚠ 这是一个**共用**模块：它被原样拷进 Blender 也要能跑，所以只依赖标准库 + 产品 core。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from ba_mod_maker.core.asset import ufs

__all__ = [
    "AssetError",
    "Node",
    "Reader",
    "SerializedFileInfo",
    "find_node",
    "node_names",
    "object_ids",
    "open_bundle",
    "read_object",
    "read_serialized",
]

#: 容器读取器（按需解块，不会把整个解压流塞进内存）。
Reader = ufs.UfsReader
#: 容器里的一个节点。
Node = ufs.Node
#: SerializedFile 的对象表摘要。
SerializedFileInfo = ufs.SerializedFileInfo
AssetError = ufs.AssetError


def open_bundle(path: str | Path) -> ufs.UfsReader:
    """打开一个 UnityFS 容器（bundle / data.unity3d）。用 with 收尾。"""
    return ufs.UfsReader(path)


def read_serialized(reader: ufs.UfsReader, node: ufs.Node) -> ufs.SerializedFileInfo:
    """读一个 SerializedFile 节点的对象表。"""
    return ufs.read_serialized_file(reader, node)


def find_node(reader: ufs.UfsReader, path: str) -> ufs.Node:
    """按节点路径取节点；没有就抛 AssetError。"""
    return reader.node(path)


def node_names(reader: ufs.UfsReader) -> list[str]:
    """容器里所有节点的路径（诊断与「找对哪个节点」用）。"""
    return [node.path for node in reader.nodes]


def object_ids(info: ufs.SerializedFileInfo) -> list[int]:
    """节点里所有对象的 path_id。"""
    return [pid for _i, pid, _stored, _size, _tid in info.objects]


def iter_objects(info: ufs.SerializedFileInfo) -> Iterator[tuple[int, int, int]]:
    """逐个产出 (path_id, 解压流绝对起点, 字节数)。"""
    for _i, pid, stored, size, _tid in info.objects:
        yield pid, info.node.offset + info.data_offset + stored, size


def read_object(reader: ufs.UfsReader, info: ufs.SerializedFileInfo, path_id: int) -> bytes | None:
    """按 path_id 读一个对象的字节；没有这个 path_id 时返回 None。"""
    for pid, start, size in iter_objects(info):
        if pid == path_id:
            return reader.read_range(start, size)
    return None
