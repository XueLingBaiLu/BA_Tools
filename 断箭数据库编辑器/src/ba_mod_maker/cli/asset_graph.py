r"""`assets graph` 子命令：一个地址键的**资产目录**（只读）。

    ba-mod-maker assets graph RU_T72B

## 它回答什么

「这台车用了哪些模型包 / prefab / 渲染器 / 材质 / 贴图 / VT 池」——
每条都带 **可读名 + pathID + 出处（在哪个包里）**。
可读名给人看；pathID 给机器用（kb/20 §5 坑 2：Mesh 的「文件名」不是身份）；
出处回答「改一颗会牵动谁」（kb/20 §1.1：一颗履带料被 9 个 prefab 引用）。

键 = **Units.ModelFileName 的原样值**（例 RU_T72B），
⛔ 不是显示名（T-72B 是 Units.HUDName；全库 543 行里 38 组重名、涉及 82 行 —— AGENTS §0.13）。

## ⛔ 它不做什么

* **只读**：不开写通道、不建补丁柜、不碰 catalog —— 一个字节都不写；
* 不跟「按数据库点亮零件」那一跳（Modifications / Turrets）—— 那是另一件事；
* ⛔ 取不到键时**不印空表**：stderr 一句人话 ＋ 退出码 2
  （空表会被读成「这台车没有资产」，而真相是「你敲的键不在 catalog 里」）。

## 退出码

| 码 | 什么时候 |
|---|---|
| 0 | 读出来了（条目可能很少，但一定是真的） |
| 2 | 用不了：没给键 / 找不到游戏根 / 键不在 catalog / 命中不唯一 / 包读不出来 |

⚠️ 没有 1：本命令是**盘点**，不是**校验** —— 它没有「有红」那类判据。
（与 finder / validator 那套「0 干净 / 1 有红 / 2 用不了」并列时，这里只用到 0 与 2。）
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ba_mod_maker.cli.emit import Emitter
from ba_mod_maker.core.errors import BaModMakerError

#: 退出码。0 读出来了 / 2 用不了（见模块头那张表）。
EXIT_OK = 0
EXIT_UNUSABLE = 2


def _game_root(text: str | None) -> Path | None:
    """游戏根：`--game` 优先，否则自动探测。找不到返回 None（**不是异常**）。"""
    from ba_mod_maker.core.assets import game as game_mod

    if text:
        root = Path(text)
        return root if game_mod.is_game_root(root) else None
    return game_mod.find_game_root()


def run(args: argparse.Namespace) -> int:
    """跑一遍：地址键 -> 资产目录。返回退出码（0 / 2）。

    :param args: 至少要有 `.key`；`.game` / `.json_out` 可选
        （接线处再加 `.project_root` 也无妨 —— 本命令不写盘，用不到它）。
    """
    emitter = Emitter(json_mode=bool(getattr(args, "json_out", False)))
    key = str(getattr(args, "key", "") or "").strip()
    if not key:
        emitter.failure(
            "要给一个地址键",
            hint="ba-mod-maker assets graph <键>；键 = Units.ModelFileName 的原样值（例 RU_T72B）",
        )
        return EXIT_UNUSABLE

    game_root = _game_root(getattr(args, "game", None))
    if game_root is None:
        emitter.failure(
            "找不到游戏根目录",
            hint="用 --game 显式给一个（例 ...\\steamapps\\common\\broken_arrow）",
        )
        return EXIT_UNUSABLE

    # 延迟 import：--help 与其它子命令不必把 UnityPy 拉起来。
    from ba_mod_maker.core.assets.graph import build_unit_graph

    try:
        graph = build_unit_graph(game_root, key)
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    except OSError as exc:
        emitter.failure(f"读包时出错：{exc}", hint="包可能正被扫描/占用 —— 重试一次")
        return EXIT_UNUSABLE

    for line in graph.lines():
        emitter.line(line)
    emitter.document(graph.document())
    return EXIT_OK


__all__ = ["EXIT_OK", "EXIT_UNUSABLE", "run"]
