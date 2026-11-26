r"""改几何的两把纯函数尺子：`payload_diff` 与 `vertex_span`。

**来源（逐字节照搬）**：`ba_mod_maker/cli/mesh_edit.py` 第 283-307 行。
那条 CLI（`mesh` 命令组）已于 2026-10-25 按用户口径「全删」退役，但这两件是**纯函数、
零 CLI 状态**，所以搬到 asset_kernel 留着给还活着的读数用。

| 函数 | **活**调用方（2026-10-25 22:2x 实测） |
|---|---|
| `payload_diff` | `cli/anim.py:613/669` · `blender_addon/xform_entry.py:944` |
| `vertex_span`  | **无**（原调用方 `mesh_entry.py` 已整文件删除） |

两处 `payload_diff` 各自量的是「写回后哪些字节变了」（动画 payload / 物体 Transform）。

⚠ `vertex_span` 现在**没有活调用方** —— 机械读数：全仓 `src/` `tests/` `blender_addon/`
`tools/` 里只有它自己的 def 与本文档提到它。保留它是因为它是「其余顶点逐字节不变」
那把尺子的**定义域口径**，随包备查。

★ 2026-10-25 Lead 裁定 **(K) 保留**：它不是「不可达的产品代码」，而是一句**可复算的判据定义**
（`[起,止)` 由 `mesh.attribute_channel` + `stream_layout` 精确给出）⇒ 别按「0 调用」把它删掉。
⛔ 也别在别处再写一份等价实现：全仓算「第 i 个顶点占哪一段」的只有本模块这一处。

## 与原件唯一的差别（⛔ 函数体逐字未改）

原件把 `mesh` 模块的 import 放在 `vertex_span` 函数体内（延迟 import）。
原位置在 `cli/` 下有 PLC0415 的 per-file-ignore，搬到 `asset_kernel/` 后没有那条豁免，
所以把**同一句 import 提到模块顶部**（`mesh` 不反向依赖本模块，无环）。
两版对调用方的行为完全相同：`vertex_span(tree, i)` 的返回值逐字节一致。
自证：`_work/d2a_verify_copy.py` 比对原 283-307 行的 sha256。

⛔ 不许在这里加 CLI 状态 / argparse / Emitter —— 这里只留这两把尺子。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ba_mod_maker.asset_kernel import mesh as mesh_mod


def payload_diff(before: bytes, after: bytes) -> tuple[int, ...]:
    """两份字节里**不同**的下标（长度不等时返回空元组 —— 那种情况另有判据）。"""
    if len(before) != len(after):
        return ()
    return tuple(i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b)


def vertex_span(tree: Mapping[str, Any], index: int) -> tuple[int, int]:
    """第 index 个顶点的**位置**在顶点流里占的那一段 [起, 止)。

    这是「其余顶点逐字节不变」那把尺子的定义域：差出来的字节必须全落在这一段里。
    """
    vertex_data = tree["m_VertexData"]
    channels = list(vertex_data["m_Channels"])
    count = int(vertex_data["m_VertexCount"])
    channel = mesh_mod.attribute_channel(channels, mesh_mod.ATTRIBUTE_POSITION)
    if channel is None:
        raise ValueError("这个网格没有位置通道")
    strides, bases = mesh_mod.stream_layout(channels, count)
    stride = strides[int(channel["stream"])]
    start = bases[int(channel["stream"])] + int(channel["offset"]) + index * stride
    dim = int(channel["dimension"]) & 0xF
    return start, start + dim * mesh_mod.FORMAT_SIZE[int(channel["format"])]