r"""把共用内核（ba_mod_maker）挂进 sys.path —— **必须在其它子模块被 import 之前生效**。

## 为什么单独一个模块

blender_addon/__init__.py 里那批 from . import 一执行就会 import ba_mod_maker；
要把路径先挂上，只能在它们**之前**先 import 本模块。而 import 语句必须留在文件顶部
（否则 ruff E402），所以「挂路径」这件事只能放进一个独立模块自己的模块级代码里。

## 为什么必须有这一步（真机实测）

zip 装到仓外时（用户的真实场景）import ba_mod_maker 会 ModuleNotFoundError，
插件**注册不上去**，侧栏连 BA Mod 标签都不会出现。所以 build_addon_zip.py 会把
src/ba_mod_maker 打进 zip 的 blender_addon/_kernel/，这里负责把它挂进 sys.path。

⛔ 不许静默失败：结果写在 KERNEL_STATUS 里，register() 时无条件打印。
"""

from __future__ import annotations

import os
import sys

#: 人类可读的内核来源（environment / bundled / 缺失）。
KERNEL_STATUS = ""
#: 随包分发的第三方依赖挂没挂上（打包时带进来的 _vendor/）。
VENDOR_STATUS = ""

# ★ 2026-10-12：**先把随包依赖挂上**，再探内核。
#   为什么必须有它：内核的 `core/asset/dbcryp.py` 顶层就 `from Crypto.Cipher import AES`，
#   而容器 `data.unity3d` 里 24 张表全是 AES-256-CBC —— 没有它就**读不了游戏本体**
#   （用户 2026-10-12 选定的名字表来源），只能退到「产品读一次落缓存」那条要手工跑一步的路。
#   实测（本轮）：把这份 `Crypto` 挂进 sys.path 后，**Blender 5.2.1 / CPython 3.13.13 里
#   AES-256-CBC 往返 ROUNDTRIP_OK True**（正控 venv 3.12.14 同为 True）—— 装得了。
#   ⚠ 仓里**不放二进制**：这份由 `tools/build_addon_zip.py` 打包时从解释器的
#   site-packages 取（见那里的 `vendor_members()`）。
_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendor")
if os.path.isdir(_VENDOR):
    if _VENDOR not in sys.path:
        sys.path.insert(0, _VENDOR)
    VENDOR_STATUS = f"随包依赖：{_VENDOR}"
else:
    VENDOR_STATUS = "随包依赖：无（走环境里的 Crypto / 内核）"

# ★ 2026-10-13：自带运行时是**分池**的（设计稿 §4 D2.1 ＋ `_vendor/vendor_manifest.json`）：
#   纯 Python 池 `_vendor/pure/`、原生池 `_vendor/native/cp3XX/`。
#   ⛔ 这两个池**也必须挂上** —— `runtime.py` 只负责**报**（它明确声明「不偷偷改 sys.path」），
#   光把轮子拷进包、没人挂路径，等于没带。顺序：**原生优先**（同名时用带 .pyd 的那份）。
_TAG = f"cp{sys.version_info[0]}{sys.version_info[1]}"
_POOLS = (
    os.path.join(_VENDOR, "native", _TAG),
    os.path.join(_VENDOR, "pure"),
)
_MOUNTED: list[str] = []
for _pool in reversed(_POOLS):  # 反着插，插完 native 在 pure 前面
    if os.path.isdir(_pool):
        if _pool not in sys.path:
            sys.path.insert(0, _pool)
        _MOUNTED.insert(0, _pool)
if _MOUNTED:
    VENDOR_STATUS = f"随包依赖：{_VENDOR}（池 {_TAG}：{'、'.join(_MOUNTED)}）"

try:
    import ba_mod_maker  # 只为探路：能找到就走环境那份

    KERNEL_STATUS = f"内核：environment（{ba_mod_maker.__file__}）"
except ImportError:
    _BUNDLED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_kernel")
    if os.path.isdir(os.path.join(_BUNDLED, "ba_mod_maker")):
        if _BUNDLED not in sys.path:
            sys.path.insert(0, _BUNDLED)
        KERNEL_STATUS = f"内核：bundled（{_BUNDLED}）"
    else:
        KERNEL_STATUS = "内核：**缺失**（环境里没有、插件也没自带 _kernel/）"
