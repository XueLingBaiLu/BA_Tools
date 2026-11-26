r"""⛔ **这条没有新家**（task-9 第 5 条：如实 skip，⛔ 不硬凑一条假绿）。

## 旧题目是什么

`P3-1 + P3-4`：**蒙皮入口**（顶点组 -> blend 通道）与**挂点命名表自检**。八段逐段实查：

| 段 | 旧题目 | 它 import 的模块 | 2026-10-15 实查 |
|---|---|---|---|
| A | 挂点命名表与 **841 条实读**对得上 | `mounts.py` | **已删** |
| B | 6 条挂点判据 M1~M6（一条一个违规） | `mounts.py` | **已删** |
| C | 蒙皮入口：槽号<->骨名<->顶点组 往返 | `skin_entry.py` | **已删**；规矩有人守，见下 |
| D | 夹具证据：单变量 ＋ 0 悬空 PPtr | `skin_entry.py` ＋ `_work/p3skin/` | 模块与生成脚本都已删 |
| E | 探针读数：正控一致 / 负控必须不同 | `_work/p3skin/readings.json` | 同上（脚本已删） |
| F | 面板层：命名表 / 发现画得出来 | `mount_panel.py` | **已删** |
| G | 合并（task-17）：增量只加分区 | `mounts.load_table()` | **已删** |
| H | M7「认得更多名字」 | `mounts.py` | **已删** |

## 为什么说「没有新家」而不是「搬到别处」

★ 任务板上给的落点是 `asset_kernel/skin_install.py` / `cli skin` —— **实查之后：那是另一个题目**。

```
blender_addon/ 与 src/ 里 grep WeaponPlace / mounts / 841   -> 0 命中（挂点命名表整条下线）
cli/__main__.py:  sub.add_parser("skin", help="材质槽通道：修改 units 材质槽的指向")
asset_kernel/skin_install.py  ->  皮肤线 = 材质槽换图（换的是 Texture2D 的 PPtr）
```

⇒ 旧用例的 `skin` 是**蒙皮（顶点组 / 骨槽）**，新家的 `skin` 是**材质槽换图**；同名不同物。
而蒙皮那条线的**规矩本身还活着**，落点与守它的用例是：

* `blender_addon/rig_plan.py`（骨规划，纯逻辑）＋ `blender_addon/armature.py`（bpy 那一层）；
* **`tests/test_blender_rig.py`**（2026-10-15 实跑 **15 passed**）已逐条守着它们：
  * B 段「骨槽名 = 顶点组名」＋ 负控 `RIG.missing_groups(...)`（塞假名字 -> 报出来）
    —— **这正是旧 C / D 段那条规矩**；
  * E 段「绑定姿势 == prefab 现在摆的姿势」＋ 负控「换隔壁槽的 bindpose ⇒ 对不上」
    —— **这正是旧 E 段那条规矩**；
  * D 段「骨的头 = 节点世界坐标」＋ 负控「换轴写反 ⇒ 对不上」。

⛔ 所以这里**再写一遍**只会得到「同一件事的第二把尺子」（AGENTS §5「逻辑只有一份」），
而且第二把尺子量的是 `_work/p3skin` 那批**研究产物**（生成它们的脚本已删）—— 那是假绿。

## 真要看「蒙皮那条链还在不在」去哪看

* `tests/test_blender_rig.py` —— 骨规划 ＋ 骨槽名表（15 条，含 4 条负控）；
* `tests/test_import_unit.py` —— 重活用例（真起 Blender 补骨架）；
* `blender_addon/importer.py::_make_vertex_groups`（顶点组按 SMR 骨槽号建，
  口径原先写在 `blender_addon/mesh_entry.py:567`（那个面板 2026-10-25 随 mesh 栈退役））。

## 判据一行

`pytest tests/test_blender_skin.py -q -n 0 -rs` -> **1 skipped**（本文件）；
`pytest tests/test_blender_rig.py -q -n 0` -> **15 passed**（真正守着那条链的那一份）。
"""

from __future__ import annotations

import pytest

pytest.skip(
    "旧题目（挂点命名表 841 条 / 蒙皮入口 槽号<->顶点组 往返 / 挂点面板）整条下线："
    "blender_addon/mounts.py · skin_entry.py · mount_panel.py 三个文件都没了，"
    "挂点那部分在 blender_addon/ 与 src/ 里 grep 0 命中；"
    "而蒙皮的规矩**已被 tests/test_blender_rig.py 守着**（15 passed：骨槽名=顶点组名＋"
    "missing_groups 负控、绑定姿势==摆的姿势＋隔壁骨负控、骨的头坐标＋换轴负控）"
    "⇒ 这里再写一遍就是第二把尺子。"
    "⛔ 任务板给的落点 asset_kernel/skin_install.py / cli skin 实查是材质槽换图，另一个题目。"
    "详见本文件模块头那张逐段表。",
    allow_module_level=True,
)