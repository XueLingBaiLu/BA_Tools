"""提取器自带的 Blender 侧内核（自 BA_Mod_Maker_2.0/blender_addon 迁出）。

只保留提取器真正用到的模块：importer / anim_entry 及其依赖闭包（13 个）。
原插件（panels_* / panels_common 之外那一堆 UI、装机、皮肤、特效面板）已删除。

⚠ 内核依赖 ba_mod_maker：本目录下的 _kernel/ba_mod_maker 是指向仓库 src 的 junction，
   由 _kernel_path 在其它子模块 import 之前挂进 sys.path（与原插件同一机制）。
"""
from . import _kernel_path  # noqa: F401  —— 必须最先执行（挂 sys.path）
