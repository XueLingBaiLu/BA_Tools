r"""面板的公共基类与共用工具（⛔ 面板之间不许互相 import，只走这里）。

为什么要基类：**注册顺序 = 侧栏顺序**（`__init__._CLASSES` 一处决定），而 space/region/category
这三样每个面板都写一遍就会漂；写在基类上，编号顺序就只由 `__init__._CLASSES` 一处决定。
"""

from __future__ import annotations

import os
from pathlib import Path

import bpy

#: 侧栏分类名 —— 用户在 3D 视图按 N 看到的标签。
CATEGORY = "BA Mod"


class BaPanel:
    """所有面板的公共基类：统一挂到 3D 视图侧栏的同一个标签下。"""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    #: ★ 折叠栏**默认收起**（用户口径 2026-10-14：「所有折叠栏默认折叠，给我改！」）。
    #:   写在基类上 ⇒ 各格**一处改、不会漏**；Blender 读的是类的属性，走继承链找得到
    #:   （bl_space_type / bl_category 早就这么用了，真机实测有效）。
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context) -> bool:
        return True


import unicodedata

#: 一行可见文本的**显示格**上限 —— 出处 `docs/设计-Blender插件GUI视觉规范.md` L4（40 格）。
#: ⚠ 产品里原来那个 `display_width` 在归并时**丢了**（2026-10-15 `u4` 量出 66 行超标，
#: 最长 84 格；而 Blender 的 label **只截断不换行** ⇒ 超了就是被悄悄切掉尾巴）。
TEXT_WIDTH_MAX = 40

# --------------------------------------------------------------------------- #
# ★ 2026-10-17（用户真机截图）「**不要在我完全展开的情况下，显示这些省略号**」
#
# 原来一律按写死的 40 格截 —— 那是**默认 300 px 侧栏**量出来的基准，用户把 N 侧栏拖到
# 约 1050 px 之后 `…` 还在。现在按**这一帧侧栏的实际宽度**换算（唯一一套，⛔ 各面板别自己算）：
#   · `set_draw_width(context)` 每格 draw() 开头调一次 ⇒ 把宽度换算成格数写进会话；
#   · `draw_limit()` 各画法默认用它；
#   · ⛔ 拿不到宽度（尺子 / 后台 / 没有 region）⇒ **仍是 40** ⇒ 现有 u4 判据与所有指纹不变。
# --------------------------------------------------------------------------- #
#: 基准：视觉规范 L4 的「默认 300 px 侧栏 ≈ 40 格」⇒ 每格 7.5 px。
PX_PER_CELL = 7.5
#: 再宽也不无限画（160 格 ≈ 1200 px，够拖到 1050 px 那档用）。
TEXT_WIDTH_CAP = 160
#: 面板内缩 / 滚动条 / 按钮边缘的余量（换算时先扣掉）。
DRAW_WIDTH_MARGIN = 2
#: 会话键：这一帧侧栏的**格数上限**（`set_draw_width()` 写、`draw_limit()` 读）。
DRAW_WIDTH_KEY = "draw_width_cells"
#: 内核缺失时那一句红话（**唯一一处**：⛔ 各面板别自己拼一句）。
KERNEL_MISSING = "内核缺失 —— 完整读数看 ⑤ 撤 → ⌁ 调试（📋 复制诊断）"


def _ui_scale(context=None) -> float:
    """Blender 的界面缩放（读不到按 1.0 —— ⛔ 不许因此抛）。"""
    try:
        prefs = getattr(bpy.context, "preferences", None)
        scale = getattr(getattr(prefs, "system", None), "ui_scale", 1.0)
        return float(scale or 1.0)
    except Exception:  # noqa: BLE001  —— 桩 / 后台没有 preferences：按 1.0
        return 1.0


def set_draw_width(context=None) -> int:
    """每格 `draw()` 开头调一次：把**这一帧侧栏宽度**换算成格数写进会话。

    返回换算出来的格数；**拿不到宽度（桩 / 后台 / 没有 region）⇒ 回 0**，但会话里的键会被
    清掉 ⇒ `draw_limit()` 退回 `TEXT_WIDTH_MAX`（40）—— 尺子那一份行为因此**一个字节都不变**。
    """
    width = 0
    try:
        region = getattr(context, "region", None) if context is not None else None
        width = int(getattr(region, "width", 0) or 0)
    except Exception:  # noqa: BLE001  —— 没有 region / 宽度不是数：当「量不到」
        width = 0
    if width <= 0:
        session().pop(DRAW_WIDTH_KEY, None)  # 没量到 ⇒ 回到默认 40（⛔ 不许留上一帧的旧值）
        return 0
    cells = int(width / (PX_PER_CELL * _ui_scale(context))) - DRAW_WIDTH_MARGIN
    limit = max(TEXT_WIDTH_MAX, min(TEXT_WIDTH_CAP, cells))
    set_session(**{DRAW_WIDTH_KEY: limit})
    return limit


def draw_limit() -> int:
    """当前该用多少格（没设过 = 40 —— 尺子 / 后台 / 第一帧都是它）。"""
    try:
        raw = session().get(DRAW_WIDTH_KEY)
        return int(raw) if raw else TEXT_WIDTH_MAX
    except Exception:  # noqa: BLE001
        return TEXT_WIDTH_MAX


def kernel_warning() -> str:
    """内核**缺失**时那一句红话；正常 = 空串（⛔ 正常时一个字都不画）。

    ★ 用户 2026-10-17：「验证的这一整个板块，与 Debug 重合了」—— 「内核 / 随包依赖」两行原先
    在 ① 拿 / ② 改 / ③ 验 / 素材盒 / 配置 · 起步 里**各重复一遍**，而 ⌁ 调试 里已有全量。
    现在这五处只在**出问题**时画这一行；全量读数仍看 ⑤ 撤 → ⌁ 调试（📋 复制诊断）。
    ⛔ 不许变成静默：缺失时这一行**必须**出现。
    """
    try:
        lines = kernel_lines()
    except Exception:  # noqa: BLE001  —— 连这两行都拿不到：比「正常」更严重，照样报红
        return KERNEL_MISSING
    first = str(lines[0]) if lines else ""
    if not first.strip() or "缺失" in first:
        return KERNEL_MISSING
    return ""


def display_width(text: str) -> int:
    """显示格数：CJK 全角记 2，其余记 1 —— 与规范 L4 同一口径（⛔ 不是 `len()`）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(text))


def clip(text: str, limit: int | None = None) -> str:
    """按**显示格**截断，超了补 `…`（⛔ 不按字符 —— 中文按字符截必超标）。

    `limit=None`（默认）⇒ 用 `draw_limit()`：**这一帧侧栏有多宽就画多宽**（宽面板不省略、
    窄面板仍 40、桩 / 后台仍 40）。显式传数就用那个（例抬头恒 18）。
    """
    limit = draw_limit() if limit is None else int(limit)
    out = str(text)
    if display_width(out) <= limit:
        return out
    width = 0
    cut = 0
    for i, ch in enumerate(out):
        step = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if width + step > max(1, limit - 1):  # 留一格给 `…`
            break
        width += step
        cut = i + 1
    return out[:cut] + "…"


# --------------------------------------------------------------------------- #
# ★ 用户 2026-10-13 点名：「写回结果**人话优先** —— 主面板两行，明细进折叠子面板」
#   原话：「**数据怎么这么多，看又看不懂**」。
#   ⚠ 2026-10-15：那条口径随 `panels_materials.py`/`operators.py` 下线而**消失**（用户批准补齐）。
#   名字保留成钩子（`writeback_inline_lines`），⛔ 不许再让口径只活在旧模块里。
# --------------------------------------------------------------------------- #
WRITEBACK_INLINE_LINES = 2

#: **抬头**（`draw_header`）的显示格上限 —— 出处 `docs/设计-Blender插件GUI视觉规范.md`：
#: 抬头画在**面板名左边** ⇒ 超过 18 格会把面板名挤到右边（2026-10-15 u2 尺子抓到 ④ 出 40 格）。
HEADER_WIDTH = 18


def writeback_inline_lines(result) -> list[str]:
    """写回读数 -> **主面板那两行**：① 干了什么 ② 出事怎么撤。

    其余（F2…F7 那类明细）一律进「看详情」那个默认折叠的子块 —— 这是用户点名要的形状：
    「数据怎么这么多，看又看不懂」。⛔ 不足两行就有几行给几行（别硬凑、别补空行）。
    """
    rows = [str(x) for x in (result or []) if str(x).strip()]
    if not rows:
        return []
    head = [rows[0]]
    if len(rows) > 1:
        undo = next((r for r in rows[1:] if ("撤" in r or "还原" in r or "反向" in r)), rows[1])
        head.append(undo)
    return [clip(x) for x in head[:WRITEBACK_INLINE_LINES]]


def wrap(text: str, limit: int | None = None) -> list[str]:
    """按**显示格折行**（与 `clip` 同一口径，只差"超了换行而不是补 `…`"）。

    `limit=None` ⇒ `draw_limit()`（宽度感知；尺子 / 后台 = 40）。
    """
    limit = draw_limit() if limit is None else int(limit)
    lines: list[str] = []
    line, width = "", 0
    for ch in str(text):
        step = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if width + step > limit and line:
            lines.append(line)
            line, width = "", 0
        line += ch
        width += step
    if line:
        lines.append(line)
    return lines or [""]


def label_wrapped(layout, text: str, *, icon: str = "NONE", limit: int | None = None) -> None:
    """**一行**画完（超宽补 `…`）—— ⛔ 名字叫 wrapped，但 2026-10-17 起不再折行。

    ★ 沿革（两次都被用户点名，一次一个方向，最后定在「一行」）：
      ① 2026-10-17 早：截断 ⇒ 用户「文字有很多显示不全」⇒ 改成**折行**；
      ② 同日：折行 ⇒ 用户「这些地方还是截断的，改成一行」＋「详情页也到处都是截断」
         ⇒ 折行在 300px 侧栏里就是**读起来像被截断**，而且一行一条的读数被劈成两行更难读。
    ⇒ **全插件统一：一行 + `…`**（保留函数名是为了不动几十处调用点）。
    真需要多行的成段说明，用 `wrap()` 自己拼、或写进折叠块里。
    """
    layout.row().label(text=clip(text, limit), icon=icon)


def middle_clip(text: str, limit: int | None = None) -> str:
    """中间省略（两头都留）—— 包名那种「头尾都有信息」的读数值用它，⛔ 别只砍尾巴。

    `limit=None` ⇒ `draw_limit()`（宽度感知；尺子 / 后台 = 40）。
    """
    limit = draw_limit() if limit is None else int(limit)
    out = str(text)
    if display_width(out) <= limit:
        return out
    keep = max(2, (limit - 1) // 2)
    head = clip(out, keep + 1)[:-1] if keep else ""
    tail = ""
    width = 0
    for ch in reversed(out):
        step = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if width + step > keep:
            break
        width += step
        tail = ch + tail
    return f"{head}…{tail}"


def label_oneline(layout, text: str, *, icon: str = "NONE", limit: int | None = None) -> None:
    """**一行**画完（超宽就补 `…`，⛔ 不折行）—— 用户 2026-10-17：「这些地方还是截断的，改成一行」。

    与 `label_wrapped` 的分工：**读数 / 索引这类一行一条的东西**用它；
    成段说明（真需要读全的）才用 `label_wrapped`。
    """
    layout.row().label(text=clip(text, limit), icon=icon)


def reading_oneline(layout, label: str, value: str, *, limit: int | None = None) -> None:
    """一行「名称：读数」（超宽补 `…`，⛔ 不折行）。"""
    layout.row().label(text=clip(label + "：" + str(value), limit))


def reading(layout, label: str, value: str) -> None:
    """画**一行**「名称：读数」（超宽补 `…`，⛔ 不折行）—— 面板出口读数统一长这样。"""
    layout.row().label(text=clip(label + "：" + str(value)))


def kernel_lines() -> list[str]:
    """内核 / 随包依赖的状态行 —— 每个面板都可能要显示它（⛔ 不许静默失败）。"""
    from . import _kernel_path

    return [str(_kernel_path.KERNEL_STATUS), str(_kernel_path.VENDOR_STATUS)]


# --------------------------------------------------------------------------- #
# * 2026-10-17（用户裁定）「写死的绝对路径肯定要修」
#
# 插件里原来有 11 处写死的开发机工作区路径。装成 zip 到别人机器上时
# 那些路径**根本不存在**，而有的地方是**无条件读**（⑤ 的台账 / ⑨ 的夹具）⇒ 要么抛、
# 要么画一块空白。现在收成**唯一一套**解析器：解析到就用，解析不到就 None，
# 由各处画一句人话（⛔ 不猜 cwd、⛔ 不静默空、⛔ 不抛 FileNotFoundError）。
# --------------------------------------------------------------------------- #
#: 环境变量：指到**工作区根**（里面有 BA_Mod_Maker_2.0/）**或**仓根（里面有 src/ba_mod_maker）
#: —— 两种形状都认（按哪个成立归一）。
WORKSPACE_ENV = "BA_MOD_MAKER_WORKSPACE"
#: 仓目录名（工作区根下面那一层）。
REPO_DIRNAME = "BA_Mod_Maker_2.0"
#: 认「这是仓根」的那个相对路径（<根>/src/ba_mod_maker 在 = 仓根）。
KERNEL_MARK = ("src", "ba_mod_maker")
#: 默认路径来自开发机时面板上那句话（**唯一一处**：⛔ 各面板别自己写一句）。
DEFAULT_PATH_HINT = "默认工程路径来自开发机，找不到就自己填"
#: 工作区解析不到时「怎么修」那一句（同上，唯一一处）。
WORKSPACE_HINT = "设 " + WORKSPACE_ENV + " 指到工作区根"

#: 解析结果缓存（⛔ 别每帧 rglob）。键 = start；产品侧不传 start ⇒ 只有一个键。
_WORKSPACE_CACHE: dict[str, Path | None] = {}


def reset_workspace_cache() -> None:
    """清掉解析缓存 —— **只有用例要它**（换环境变量 / 换假树时）。"""
    _WORKSPACE_CACHE.clear()


def _has_kernel(path) -> bool:
    """这一级是不是**仓根**（src/ba_mod_maker 在）。"""
    try:
        return Path(path).joinpath(*KERNEL_MARK).is_dir()
    except OSError:  # —— 权限 / 怪字符：当它不成立
        return False

def _normalize_workspace(candidate):
    """一个候选路径 -> (工作区根, 仓根)；认不出形状就 (None, None)。

    两种形状（用户口径）：
    * 给的是**仓根**（src/ba_mod_maker 在）⇒ 工作区根 = 它的上一层；
    * 给的是**工作区根**（BA_Mod_Maker_2.0/src/ba_mod_maker 在）⇒ 原样。
    """
    if candidate in (None, ""):
        return (None, None)
    try:
        root = Path(str(candidate)).expanduser().resolve()
    except OSError:  # —— 不存在的盘 / 坏路径：当它不成立
        return (None, None)
    if _has_kernel(root):
        return (root.parent, root)
    if _has_kernel(root / REPO_DIRNAME):
        return (root, root / REPO_DIRNAME)
    #: 2026-11-04：用户把仓改名成「断箭数据库编辑器」⇒ 工作区根下面**按结构找**
    #: （谁有 src/ba_mod_maker 谁就是仓），不再靠 REPO_DIRNAME 那一个名字。
    try:
        for child in sorted(root.iterdir()):
            if child.is_dir() and _has_kernel(child):
                return (root, child)
    except OSError:
        pass
    return (None, None)


def workspace_root(start=None):
    """工作区根（里面有 BA_Mod_Maker_2.0/src/ba_mod_maker）—— 解析不到就 None。

    口径（用户裁定后定的唯一一套，⛔ 各面板别另写）：
    ① 环境变量 BA_MOD_MAKER_WORKSPACE 设了 ⇒ 用它（工作区根 / 仓根**两种形状都认**）；
    ② 否则从 start（默认本文件）**往上逐级**找：某级有 BA_Mod_Maker_2.0/src/ba_mod_maker
       ⇒ 那级是工作区根；某级有 src/ba_mod_maker ⇒ 那级是仓根、工作区根 = 父级；
    ③ 都不成立 ⇒ None（⛔ 不猜 cwd：装成插件之后这些开发机路径本来就不该存在）。

    start 只为**可测**（用例拿 tmp_path 造树）；产品侧一律不传。
    """
    key = "<default>" if start is None else str(start)
    if key in _WORKSPACE_CACHE:
        return _WORKSPACE_CACHE[key]
    found = None
    env = os.environ.get(WORKSPACE_ENV, "").strip()
    if env:
        found = _normalize_workspace(env)[0]
    if found is None:
        try:
            here = Path(str(start)).expanduser().resolve() if start is not None else Path(__file__)
        except OSError:  # —— 坏 start：退回本文件
            here = Path(__file__)
        for candidate in (here, *list(here.parents)):
            workspace, _repo = _normalize_workspace(candidate)
            if workspace is not None:
                found = workspace
                break
    _WORKSPACE_CACHE[key] = found
    return found


def repo_root(start=None):
    """仓根 = workspace_root()/BA_Mod_Maker_2.0 —— 解析不到（或那一层不在）就 None。"""
    workspace = workspace_root(start)
    if workspace is None:
        return None
    #: 2026-11-04：仓名不写死 —— 直接复用结构判定（_normalize_workspace）。
    _ws, repo = _normalize_workspace(workspace)
    return repo


def default_path_hint() -> str:
    """默认路径的来头：解析到工作区 = 空串；解析不到就回那句提示（⛔ 不静默）。"""
    return "" if workspace_root() is not None else DEFAULT_PATH_HINT

# --------------------------------------------------------------------------- #
# 会话级共享状态 —— 面板之间**只能经这里**交换数据
# --------------------------------------------------------------------------- #
#: 与游戏同版的 Unity 编辑器 —— **默认值**（用户可在 ① 里改，改完进会话）。
UNITY_EDITOR = Path("D:/Unity/6000.3.24f1/Editor/Unity.exe")
#: 工作区里那个素材线 Unity 6 构建工程的相对位置（默认值的**形状**，不是开发机的字面量）。
UNITY_PROJECT_RELATIVE = ("_work", "build6")


def unity_project_default() -> Path:
    """素材线的 Unity 6 构建工程 —— **默认值**（用户可在 ① 里改，改完进会话）。

    ★ 2026-10-17：原来是写死的开发机绝对路径。现在：解析到工作区 ⇒ <仓根>/_work/build6；
    解析不到（装成插件之后）⇒ 用**插件包自己的上一层**当基准，保住「绝对路径」这个形状。
    ⛔ 不许变成 None：它只是默认值，None 会让 Unity 启动逻辑分不清「没配」和「配错了」。
    面板上另有一行提示（`default_path_hint()`）告诉用户「这是开发机的默认值」。
    """
    repo = repo_root()
    base = Path(repo) if repo is not None else Path(__file__).resolve().parent.parent
    return base.joinpath(*UNITY_PROJECT_RELATIVE)

#: 会话键：Unity 两条路径（① 的两颗按钮写它，⑤/其他格读 `unity_editor()`）。
EDITOR_KEY = "unity_editor"
PROJECT_KEY = "unity_project"

#: 会话状态。**为什么需要它**：规则是「面板之间不许互相 import」，
#:   所以「⑤ 造出来的包在哪、⑥ 要读」这类数据必须有个公共落点 —— 就是这里。
_SESSION: dict[str, object] = {}


def session() -> dict[str, object]:
    """取会话状态（首次调用时填默认值）。**改它请用 set_session**，别直接改字典。"""
    if not _SESSION:
        _SESSION.update(
            {
                "game_root": None,  # ① 起步 · 环境 探到的游戏根
                "unity_editor": str(UNITY_EDITOR),
                "unity_project": str(unity_project_default()),
                "last_bundle": None,  # ⑤ 打包 · Unity 造出来的包，⑥ 出厂闸读它
                "last_gate": None,  # ⑥ 的读数摘要
            }
        )
    return _SESSION


def set_session(**kw: object) -> None:
    """写会话状态（面板之间唯一的写入口）。"""
    session().update(kw)


#: 补丁柜的目录名 —— 规范在 `session.PROJECT_DIRNAME`（同一个字，⛔ 别改一处）。
PROJECT_DIRNAME = "BAModMaker"


def session_layer():
    """`blender_addon.session`（四个根的唯一出处）或 None —— **按需 import**。

    ⛔ 不写成模块级 import：`session` 顶层就要 `ba_mod_maker.asset_kernel`，
    内核没到 / 装成 zip 但没带 `_kernel/` 时，模块级 import 会让**整个插件注册不上去**
    （`_kernel_path.KERNEL_STATUS` 明确留着「缺失」那条死路 —— 那条路上面板仍要画得出来）。
    """
    try:
        from . import session as layer
    except Exception:  # noqa: BLE001  —— 内核缺失 / 不在包上下文：面板退化成「没得查」
        return None
    return layer


def _project_root_default() -> Path:
    r"""补丁柜的落点（`<它>/patches`）—— **不许跟着当前目录漂**。

    ★ **2026-10-15 用户真机撞到的 bug**：GUI Blender 的「当前工作目录」是**启动它的那个目录**
    （他的是 `D:\Blender`）⇒ 一次写回把补丁落到了 `D:\Blender\patches`；
    「一键还原」也按同一个默认去找 ⇒ **本次会话能撤，换个地方启动就再也找不到**。
    补丁柜是「撤不撤得回去」的命根子，所以给它一个**确定、看得见**的地方：

    * 开发树里（`blender_addon/` 与 `src/` 同一个父目录）⇒ 还是**仓根**（跑测试 / 自检不变）；
    * 装成插件的（没有 `src/`）⇒ **`<游戏所在盘>\.bamod-maker\project`**。

    ★★ **2026-10-15 真机事故（用户点「写回…」报 rc=2）**：我第一版把这里的落点写成
    `%LOCALAPPDATA%\BAModMaker\project`（**C 盘**），而游戏在 **D 盘** —— 产品有一条
    **机械红线** `reverse_delta.assert_same_drive(target, game_root)`：
    「补丁柜在 C 盘，游戏在 D 盘」当场拒写（历史教训：备份落 C 盘白占 6.95 GB）。
    ⇒ 落点**必须跟着游戏所在盘**。

    唯一实现是会话层那一份（`session.default_project_root(game_root=…)`）——本函数只有转发。
    ⛔ 这不是第二份根解析：内核缺失时插件整体不可用，回落只为不让面板整格空白
    """
    layer = session_layer()
    if layer is None:
        # 只在 `session_layer()` 拿不到会话层那条死路里跑（ImportError / 内核缺失，见它的实现）：
        # 退回**旧落点**（当前目录），只保证「面板画得出来」
        return Path.cwd()
    # ★ 唯一实现：`session.default_project_root(game_root=…)`
    #   （开发树 ⇒ 仓根；装成插件 ⇒ 跟游戏同盘）。
    return layer.default_project_root(game_root=game_root())


#: **旧默认落点**：当前工作目录 —— 只用来兼容已经写在那边的那份补丁。
#: ⛔ 新写入一律走 `project_root_default()`；它是**常量式的旧落点**（`Path.cwd`），
#: 不是第二个函数体；「找已有的那份」那三跳已经并进 `session.find_project_root`。
project_root_legacy = Path.cwd


def _find_project_root() -> Path:
    r"""真去找一份**已有的**补丁柜：确定的那个 → 当前目录（旧默认）→ C 盘那份（更早的版本）。

    （2026-10-15 一晚上换了两次落点，每次都留下已经写出去的补丁：`D:\Blender\patches`
    与 `%LOCALAPPDATA%\BAModMaker\project` —— ⛔ 换了落点也必须**找得到旧的**，
    否则「一键还原」会突然说「没有可还原的目标」，而用户的包已经被改过。）

    ★ 2026-10-18：那三跳**逐字**并进了 `session.find_project_root(game_root=…)`（同一份实现，
    判据 `^def project_root` 全插件只剩会话层那一个）——本函数只有转发。
    ⛔ 这不是第二份根解析：内核缺失时插件整体不可用，回落只为不让面板整格空白
    """
    layer = session_layer()
    if layer is not None:
        return layer.find_project_root(game_root=game_root())
    # 只在 `session_layer()` 拿不到会话层那条死路里跑（ImportError / 内核缺失）：给确定的那一个落点
    return _project_root_default()


#: 名字保留（别处按 `panels_common.project_root_default()` / `find_project_root()` 调）——
#: 实现只有会话层那一份 ⇒ 这里是**别名**，⛔ 不是第二个函数体。
project_root_default = _project_root_default
find_project_root = _find_project_root


def game_root() -> Path | None:
    """游戏根 —— 走产品自己的 find_game_root（两个名字都认），结果缓存在会话里。

    `catalog.json`（≤1.2.0）与 `catalog.bin`（≥1.2.1）都算，
    见 `core/assets/game.py::CATALOG_NAMES`。
    """
    cached = session().get("game_root")
    if cached is not None:
        return Path(str(cached))
    try:
        from ba_mod_maker.core.assets.game import find_game_root
    except Exception:  # noqa: BLE001  —— 内核缺失时**不抛**，让面板画出「没找到」
        return None
    root = find_game_root()
    if root is not None:
        set_session(game_root=str(root))
    return root


def unity_editor() -> Path:
    """当前 Unity 编辑器路径：**会话里有就用会话的**，没有就用默认值。"""
    raw = session().get(EDITOR_KEY)
    return Path(str(raw)) if raw else UNITY_EDITOR


def unity_project() -> Path:
    """当前 Unity 工程路径（同上）。"""
    raw = session().get(PROJECT_KEY)
    return Path(str(raw)) if raw else unity_project_default()


def set_unity(*, editor: str | None = None, project: str | None = None) -> None:
    """改 Unity 两条路径（① 的两颗按钮调它）。传 None 的那条不动。"""
    if editor is not None:
        set_session(**{EDITOR_KEY: str(editor)})
    if project is not None:
        set_session(**{PROJECT_KEY: str(project)})


def reset_unity() -> None:
    """恢复成默认那两条（本机实测路径）。"""
    set_session(**{EDITOR_KEY: str(UNITY_EDITOR), PROJECT_KEY: str(unity_project_default())})


# --------------------------------------------------------------------------- #
# ★ 详情开关（用户口径 2026-10-14：「不要给我显示过多乱七八糟、看又看不懂的信息」）
#
# 口径：**面板默认只画摘要（≤6 行）**；逐条明细 / 十六进制 / 通道名拼接 / `m_*` 字段名 /
#   源码 sha / 设计稿出处 —— 一律收进「看详情」，默认全关。
#   ⛔ 明细**不许删数据**：它仍在会话 store 里，面板与探针读的是同一批字符串。
# --------------------------------------------------------------------------- #
#: 会话里那张「哪一格正在看详情」的表。
DETAIL_KEY = "detail_open"


#: 会话键：**正在跑的重活**那一句话（面板抬头会把它画出来）。
BUSY_KEY = "busy_note"


def mesh_sha(obj) -> tuple:
    """`(sha256 前 16 位, 顶点数)` —— 这颗网格**现在**的顶点坐标指纹；不是网格就 `("", 0)`。

    ★ 为什么要有它（2026-10-15 用户问「我为什么需要核对呢？我能直接在 Blender 里看到模型」）：
    他说得对 —— 「我动了没有」不该让他点按钮去问。所以 ① 拿 时把**当时的指纹**记下来，
    面板隔一会儿对一次：**变了没有、变了多少**，都不用开包、也不用点「核对」。
    用 `foreach_get` 一次取整块（比逐个顶点快两个数量级），44k 顶点约几十毫秒。
    """
    mesh = getattr(obj, "data", None)
    if mesh is None or getattr(obj, "type", "") != "MESH":
        return ("", 0)
    try:
        import array
        import hashlib

        count = len(mesh.vertices)
        if not count:
            return ("", 0)
        buf = array.array("f", bytes(12 * count))
        mesh.vertices.foreach_get("co", buf)
        return (hashlib.sha256(buf.tobytes()).hexdigest()[:16], count)
    except Exception:  # noqa: BLE001  -- 拿不到就如实说「拿不到」，⛔ 不猜
        return ("", 0)


def scene_state() -> dict:
    """会话里那份「**场景现在长什么样**」的缓存（由面板挂的 timer 维护；draw() 只读它）。"""
    raw = session().get("scene_state")
    return dict(raw) if isinstance(raw, dict) else {}


def set_scene_state(**kw: object) -> None:
    """更新那份缓存（timer 里调）。"""
    now = scene_state()
    now.update(kw)
    set_session(scene_state=now)


def begin_busy(note: str) -> None:
    """把「正在干什么」挂到会话上 —— 用户点完按钮**下一帧**就能看见它。

    ★ 2026-10-15 用户真机：`写回` 是**主线程阻塞**的（开 3.4 GB 包 → 写块 → 重开包逐顶点读回复核），
    Windows 过几秒就把窗口标成「未响应」—— 用户分不清「在干活」和「死了」。
    ⛔ 光在这里设值没用：Blender 点完按钮**不会先重画**再执行算子。
    真正让这句话上屏的是 `panels_edit._run_later()`：它把重活**挂到下一帧的 timer**，
    这一帧先把状态画出来。
    """
    set_session(**{BUSY_KEY: str(note)})


def end_busy() -> None:
    """重活跑完 / 抛了 —— 清掉那句话。"""
    set_session(**{BUSY_KEY: ""})


def busy_note() -> str:
    """正在跑的那句人话（空串 = 没在跑）。"""
    return str(session().get(BUSY_KEY) or "")


def draw_busy(layout) -> None:
    """在面板最上面画「正在干什么」—— ⛔ 不静默：卡住的时候用户就靠这一行。"""
    note = busy_note()
    if note:
        box = layout.box()
        label_wrapped(box, "⏳ " + note[:70], icon="TIME")


def detail_on(pid: str) -> bool:
    """这一格现在是不是「看详情」状态（默认 False = 只画摘要）。"""
    return bool((session().get(DETAIL_KEY) or {}).get(pid, False))


def toggle_detail(pid: str) -> None:
    """翻这一格的详情开关（存会话）。"""
    table = dict(session().get(DETAIL_KEY) or {})
    table[pid] = not table.get(pid, False)
    set_session(**{DETAIL_KEY: table})


def redraw_all() -> None:
    """让 3D 视图重画（拿不到窗口就算了，⛔ 不抛）。"""
    try:
        for window in bpy.context.window_manager.windows:  # type: ignore[attr-defined]
            for area in window.screen.areas:
                area.tag_redraw()
    except Exception:  # noqa: BLE001  —— 后台模式没有窗口
        return


def detail_row(layout, pid: str) -> None:
    """画那颗「看详情 ▸ / 收起详情 ▾」（各格共用**同一颗**算子）。"""
    row = layout.row()
    text = "收起详情 ▾" if detail_on(pid) else "看详情 ▸"
    # ★ 图标是**必填**（视觉规范 u7：每颗算子都要带 `icon=`；2026-10-15 test_gui_look 量出 11 处缺）
    icon = "TRIA_DOWN" if detail_on(pid) else "TRIA_RIGHT"
    row.operator("ba_mod.toggle_detail", text=text, icon=icon).panel = pid


# --------------------------------------------------------------------------- #
# ★ 折叠块（2026-10-15 五格归并）：旧「一条路」那一格的界面，装进五格里的一个块。
#
#   用户口径（2026-10-14）：「**所有折叠栏默认折叠**」⇒ 七个块 default=False。
#   ⛔ 它**不是**详情开关（那个按格存会话）：这一个存 Scene（跟着 .blend 走）——
#      「这一份工作里我要看哪几块」是文件的事，不是本次会话的事。
# --------------------------------------------------------------------------- #
#: 八个折叠块：`(开关名, 那行标题)`。顺序 = 侧栏上从上到下的顺序。
#: ⚠ 2026-10-17 第八块是「⌁ 调试」（Debug 模式，住 `panels_debug.py`，画在 ⑤ 撤 里）。
BLOCKS: tuple[tuple[str, str], ...] = (
    ("blk_start", "⌁ 配置 · 起步（环境）"),
    ("blk_box", "⌁ 素材盒"),
    ("blk_fbx", "⌁ 导出 · FBX"),
    ("blk_unity", "⌁ 打包 · Unity"),
    ("blk_gate", "⌁ 出厂闸"),
    ("blk_look", "⌁ 对拍 · 观感"),
    ("blk_anim", "⌁ 动画"),
    ("blk_debug", "⌁ 调试"),
)


class BAMOD_BlockSettings(bpy.types.PropertyGroup):
    """五格里那八个折叠块的开关（**默认全收** —— 用户口径：所有折叠栏默认折叠）。"""

    blk_start: bpy.props.BoolProperty(default=False)  # type: ignore[attr-defined]
    blk_box: bpy.props.BoolProperty(default=False)  # type: ignore[attr-defined]
    blk_fbx: bpy.props.BoolProperty(default=False)  # type: ignore[attr-defined]
    blk_unity: bpy.props.BoolProperty(default=False)  # type: ignore[attr-defined]
    blk_gate: bpy.props.BoolProperty(default=False)  # type: ignore[attr-defined]
    blk_look: bpy.props.BoolProperty(default=False)  # type: ignore[attr-defined]
    blk_anim: bpy.props.BoolProperty(default=False)  # type: ignore[attr-defined]
    blk_debug: bpy.props.BoolProperty(default=False)  # type: ignore[attr-defined]


def block_settings(context=None):
    """Scene 上那份折叠块开关（没挂上回 None —— 调用方走「永远展开」那条路）。

    ⚠ 优先用**面板自己那个** context：多窗口 / 多场景时 `bpy.context.scene` 是活动窗口那个，
    而 `draw(self, context)` 手上的才是这一格所属窗口的。给不出 context 时退回全局（⛔ 不抛）。
    """
    scene = getattr(context, "scene", None) if context is not None else None
    if scene is None:
        scene = getattr(bpy.context, "scene", None)
    return getattr(scene, "bamod_blocks", None) if scene is not None else None


def block(layout, pid: str, title: str, context=None):
    """画一个折叠块的抬头；**展开时返回往里画的那个 box**，收起时返回 None。

    ★ 为什么不用「详情开关」那一套（算子 ＋ 会话字典）：折叠状态跟着 .blend 走更合直觉，
    而且 `prop()` 是真控件 —— 点一下 Blender 自己重画，不需要 redraw_all()。
    ⛔ 挂件掉了（Scene.bamod_blocks 没注册）⇒ **永远展开**：
      一个控件都不许因为一个 PropertyGroup 没挂上就从界面上消失。
    """
    settings = block_settings(context)
    if settings is None:
        box = layout.box()
        label_wrapped(box, title, icon="TRIA_DOWN")
        return box
    open_now = bool(getattr(settings, pid, False))
    # ★ 与 `fold_row` 用**同一颗 toggle 算子**画（`row.operator`）—— 两套画法会一个灰条一个白条。
    row = layout.row(align=True)
    row.operator(
        "bamod.toggle_block", text=clip(title), icon="TRIA_DOWN" if open_now else "TRIA_RIGHT"
    ).panel = pid
    return layout.box() if open_now else None


def attach_properties() -> None:
    """把折叠块开关挂到 Scene 上（`__init__.register()` 调）。"""
    bpy.types.Scene.bamod_blocks = bpy.props.PointerProperty(type=BAMOD_BlockSettings)  # type: ignore[attr-defined]


def detach_properties() -> None:
    """摘掉（unregister 时调）。"""
    if hasattr(bpy.types.Scene, "bamod_blocks"):
        del bpy.types.Scene.bamod_blocks


class BamodToggleDetail(bpy.types.Operator):
    """看详情 / 收起详情 —— 各格共用（状态存会话）。"""

    bl_idname = "ba_mod.toggle_detail"
    bl_label = "看详情"
    bl_description = "展开/收起这一格的明细读数（默认收起；⛔ 明细不会丢，只是不画）"

    panel: bpy.props.StringProperty()  # type: ignore[attr-defined]

    def execute(self, context):
        toggle_detail(self.panel)
        redraw_all()
        return {"FINISHED"}


class BAMOD_OT_toggle_block(bpy.types.Operator):
    """折叠块开/合（状态存 `Scene.bamod_blocks`）。

    ★ 2026-10-17 用户：「**动画的折叠栏你怎么不改？**」—— 原来这块用 `row.prop(emboss=False)` 画，
    与 `fold_row`（`row.operator`）**两套画法** ⇒ 背景条/箭头/缩进都不一样。
    现在统一成同一颗算子：**画法一样、点法一样**，只是状态还留在 Scene 上（不改存储口径）。
    """

    bl_idname = "bamod.toggle_block"
    bl_label = "折叠块"
    bl_description = "展开 / 收起这一段（默认收起；⛔ 内容不会丢，只是不画）"
    bl_options = {"REGISTER", "INTERNAL"}

    panel: bpy.props.StringProperty()  # type: ignore[attr-defined]

    def execute(self, context):
        settings = block_settings(context)
        pid = str(self.panel or "")
        if settings is None or not pid or not hasattr(settings, pid):
            return {"CANCELLED"}
        setattr(settings, pid, not bool(getattr(settings, pid, False)))
        redraw_all()
        return {"FINISHED"}


#: ★ 本模块也交算子（`__init__._all_classes()` 按 `_MODULES` 收）。
OPERATORS: tuple[type, ...] = (BamodToggleDetail, BAMOD_OT_toggle_block)
#: 要 register_class 但不是算子的那些（折叠块开关那个 PropertyGroup）。
EXTRA_CLASSES: tuple[type, ...] = (BAMOD_BlockSettings,)
