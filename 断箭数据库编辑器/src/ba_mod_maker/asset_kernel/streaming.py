"""Unity 流式数据（.resS / .resource）的定位（读侧，零第三方依赖）。

## 为什么单开一个模块

Unity 把大块二进制（顶点流、贴图）**不放在对象里**，而是在对象里留一个
StreamingInfo { path, offset, size }，字节在容器里另一个节点（后缀 .resS）上。
对象里的 m_DataSize / image data 因此是**空的** —— 谁把它当「空网格 / 空图」读，
谁就得到一份**静默错件**。W2 实测就是这个坑的规模：ammoprefabs 包 86 个 Mesh 里
**79 个**的 m_DataSize 是 0 字节（工单 §7 坑 4/5），本单实测 86/86 可以解出来。

## offset 的口径（实测钉死）

StreamingInfo.offset 是**节点内**的相对偏移，不是文件偏移：

    with open_bundle(bundle) as reader:
        node = reader.node(...)                     # 节点的解压流偏移 node.offset
        raw = reader.read_range(node.offset + info.offset, info.size)

与 UnityPy 的 helpers/ResourceReader.py 逐字节一致（本机实测：ammoprefabs 包 79/79
个流式网格的 sha256 相同；units 包 3 张贴图同样相同）。

## 节点名匹配：archive:/CAB-<hash>/CAB-<hash>.resS

包里有两个节点：CAB-<hash> 与 CAB-<hash>.resS。path 里的哈希是**建包时**的，
所以按 basename 匹配，并给「哈希对不上」的现场留一条降级路（工单 §7 坑 4 的
「解包目录可能带旧哈希」）：

1. path 的 basename 精确命中（本机 units / ammoprefabs 实测**命中**，哈希逐字符相同）；
2. 退化后缀（<stem>.resource / <stem>.assets.resS / <stem>.resS，与 UnityPy 同口径）；
3. 都不中时，若容器里**恰好只有一个**流式后缀节点，就用它并记 hash_mismatch=True；
   多于一个就抛错（宁可不读，也不猜错一张图 / 一份顶点流）。

第 3 条是**唯一**允许「哈希对不上还读」的场合，而且它一定在返回物里留痕
（StreamPayload.hash_mismatch），调用方能把这件事打进报告 —— 静默降级正是
W6「跨节点换贴图」最容易踩坏的地方。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "STREAM_SUFFIXES",
    "StreamPayload",
    "StreamedDataError",
    "StreamingInfo",
    "node_candidates",
    "parse_streaming_info",
    "resolve_stream",
]

#: 流式节点的后缀（顺序 = 匹配优先级；与 UnityPy 的 ResourceReader 同口径）。
STREAM_SUFFIXES: tuple[str, ...] = (".resS", ".resource", ".assets.resS")


class StreamedDataError(ValueError):
    """流式数据定位不了 / 定位到的区间越界。

    单独一个异常类型是有意的：调用方必须**显式**处理「这块没读」，
    不许当成空数据继续往下走。带 hint 是为了跟容器层（ufs.AssetError）一个口径：
    报错要把「怎么办」也说出来。
    """

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


def _basename(path: str) -> str:
    """把 Unity 的 archive:/<dir>/<name> 形式取成 basename（两种斜杠都认）。"""
    return os.path.basename(path.replace("\\", "/"))


@dataclass(frozen=True, slots=True)
class StreamingInfo:
    """m_StreamData 那三个字段。"""

    path: str
    offset: int
    size: int

    @property
    def is_streamed(self) -> bool:
        """True = 字节不在对象里，要去 .resS 取。"""
        return bool(self.path) and self.size > 0

    @property
    def basename(self) -> str:
        return _basename(self.path)

    @property
    def stem(self) -> str:
        return os.path.splitext(self.basename)[0]

    def describe(self) -> str:
        return f"{self.basename}@{self.offset}+{self.size}"


#: 认得的字段名（typetree 的字典 / UnityPy 的 StreamingInfo 对象都用这几个）。
_PATH_KEYS = ("path", "m_Path", "Path")
_OFFSET_KEYS = ("offset", "m_Offset", "Offset")
_SIZE_KEYS = ("size", "m_Size", "Size")


def _pick(source: object, keys: Sequence[str]) -> object | None:
    if isinstance(source, Mapping):
        for key in keys:
            if key in source:
                return source[key]
        return None
    for key in keys:
        if hasattr(source, key):
            return getattr(source, key)
    return None


def parse_streaming_info(source: object) -> StreamingInfo | None:
    """把 typetree 里的 m_StreamData 归一成 StreamingInfo。

    :returns: source 是 None 时返回 None；**字段一个都认不出来**时抛
        StreamedDataError —— 那说明调用方传错了对象，静默返回 None 会让它
        变成「内联数据」，于是又一次静默读空。
    """
    if source is None:
        return None
    raw_path = _pick(source, _PATH_KEYS)
    raw_offset = _pick(source, _OFFSET_KEYS)
    raw_size = _pick(source, _SIZE_KEYS)
    if raw_path is None and raw_offset is None and raw_size is None:
        raise StreamedDataError(
            f"这不是 StreamingInfo（认不出 path/offset/size 任何一个）：{type(source).__name__}"
        )
    path = "" if raw_path is None else str(raw_path)
    offset = 0 if raw_offset is None else int(raw_offset)
    size = 0 if raw_size is None else int(raw_size)
    return StreamingInfo(path=path, offset=offset, size=size)


def node_candidates(info: StreamingInfo) -> tuple[str, ...]:
    """按优先级列出该 path 可能对应的节点 basename（UnityPy 同口径，去重保序）。"""
    base = info.basename
    stem = info.stem
    out: list[str] = []
    for name in (base, f"{stem}.resource", f"{stem}.assets.resS", f"{stem}.resS"):
        if name and name not in out:
            out.append(name)
    return tuple(out)


#: 后缀的小写形式（比对时两边都转小写 —— 「.resS」那个大写 S 是真踩过的坑）。
_LOWER_SUFFIXES = tuple(s.lower() for s in STREAM_SUFFIXES)


def _stream_nodes(nodes: Iterable[object]) -> list[object]:
    return [
        node
        for node in nodes
        if str(getattr(node, "path", "")).lower().endswith(_LOWER_SUFFIXES)
    ]


@dataclass(frozen=True, slots=True)
class StreamPayload:
    """一次流式定位的**完整证据**（字节 + 怎么定到的）。"""

    info: StreamingInfo
    node_path: str
    node_offset: int
    data: bytes
    matched_by: str
    hash_mismatch: bool

    @property
    def size(self) -> int:
        return len(self.data)

    def describe(self) -> str:
        tail = "（哈希与节点名不一致，走的是唯一后缀降级）" if self.hash_mismatch else ""
        return (
            f"{self.info.basename} -> {_basename(self.node_path)}"
            f"@{self.info.offset}+{self.info.size} 命中方式={self.matched_by}{tail}"
        )


def resolve_stream(bundle: object, stream_data: object) -> StreamPayload:
    """把 m_StreamData 解成字节（工单 §9 点名的读侧入口）。

    :param bundle: 打开的容器读取器（asset_kernel.unityfs.open_bundle 的返回值）：
        只要有 .nodes（每项 .path / .offset / .size）与 .read_range(offset, size)。
    :param stream_data: typetree 里的 m_StreamData，或 UnityPy 的 StreamingInfo
        （任何带 path/offset/size 的对象都认）。
    :raises StreamedDataError: 没有流式信息 / 节点找不到 / 区间越界。
    """
    info = parse_streaming_info(stream_data)
    if info is None or not info.is_streamed:
        raise StreamedDataError("没有流式数据：path 为空或 size 为 0（内联对象不该走这条）")
    nodes = list(getattr(bundle, "nodes", ()))
    if not nodes:
        raise StreamedDataError("容器里没有节点表（bundle 不是 UfsReader？）")

    by_name: dict[str, object] = {}
    for node in nodes:
        by_name.setdefault(_basename(str(getattr(node, "path", ""))), node)

    node = None
    matched_by = ""
    hash_mismatch = False
    for candidate in node_candidates(info):
        hit = by_name.get(candidate)
        if hit is not None:
            node = hit
            matched_by = "basename" if candidate == info.basename else f"alias:{candidate}"
            break
    if node is None:
        # 哈希对不上：只有在流式后缀节点**唯一**时才敢用（工单 §7 坑 4）。
        stream_nodes = _stream_nodes(nodes)
        if len(stream_nodes) == 1:
            node, matched_by, hash_mismatch = stream_nodes[0], "single-stream-node", True
        else:
            raise StreamedDataError(
                f"找不到流式节点 {info.basename!r}；容器里有 {len(nodes)} 个节点，"
                f"其中流式后缀节点 {len(stream_nodes)} 个："
                f"{[_basename(str(getattr(n, 'path', ''))) for n in nodes]}",
                hint="一个包有多个 .resS 时不许猜（会读错图 / 错顶点流）",
            )

    node_offset = int(getattr(node, "offset", 0))
    node_size = int(getattr(node, "size", 0))
    if info.offset < 0 or info.size <= 0:
        raise StreamedDataError(f"流式区间不合法：offset={info.offset} size={info.size}")
    if info.offset + info.size > node_size:
        raise StreamedDataError(
            f"流式区间越界：{info.describe()} 落在节点 "
            f"{_basename(str(getattr(node, 'path', '')))}（{node_size} B）之外",
            hint="节点名匹配对了但偏移错了 —— 别把这份字节当数据用",
        )
    data = bytes(bundle.read_range(node_offset + info.offset, info.size))
    if len(data) != info.size:
        raise StreamedDataError(f"只读到 {len(data)} B，声明 {info.size} B")
    return StreamPayload(
        info=info,
        node_path=str(getattr(node, "path", "")),
        node_offset=node_offset,
        data=data,
        matched_by=matched_by,
        hash_mismatch=hash_mismatch,
    )
