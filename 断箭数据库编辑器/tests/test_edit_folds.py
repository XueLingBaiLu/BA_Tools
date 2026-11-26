r"""② 改 的折叠栏（用户 2026-10-16 点名：「一个屏幕里的信息太多了」）—— 机械判据。

## 这条规矩

分组可折叠；**材质 / 贴图段默认展开**，其余默认收起；折叠状态存**会话**（切面板不丢）；
⛔ 不改任何已有控件的名字 / 相对顺序 —— 折叠只是**不画**，不是删。

## 判据一行

    .venv\Scripts\python.exe -m pytest tests/test_edit_folds.py -o addopts= -m "not slow" -q -n 0
      -> 「默认展开的段」0（改动前一处折叠都没有）-> 1（材质 / 贴图段）；
         「全展开时 ② 改 画出来的东西」逐条 == 改动前那一条序列 ＋ 5 组折叠抬头。

## 独立旁证（2026-10-16 真跑，用的是**产品尺子自己**那两趟 JSON）

    .venv/Scripts/python.exe _work/panelsplit/draw_all.py <baseline>   # 改动前
    .venv/Scripts/python.exe _work/panelsplit/draw_all.py <after>      # 改动后
      -> ② 改 改动前 **188 条**；改动后 **208 条**（＝ 188 ＋ 5 组抬头 × 4 条）；
         把 5 组抬头剥掉 ⇒ **sha256 逐位相同**（7eed044bed439193…），两趟 error 都是空。

## task-7（本轮）—— 同一把尺子，判据换成「改名回滚」

    折叠 7 组（材质段里多了「用我的图」「补接游戏原图」两个子折叠，⛔ 都默认收起）
      -> 条数：全收起 66 / 默认态 103 / 全展开 216（= 188 ＋ 7 组抬头 × 4 条）
      -> 剥掉 7 组抬头：188 条 / 70105635ab38f3dc…（本轮）
      -> 再把这轮那处改名换回旧文案：**d4cb064f… 逐位相同**
         ⇒ 本轮在探针那趟**只动了那一处文案** —— 控件一个没多、一个没少、一条没挪。
    状态行（本轮新加）：两路都接了**真图** ⇒ 「已接上：法线 / Mask（无需再点）」＋ 两行「← 哪张」；
      没接 / 只接一路 / 接的是**占位图** ⇒ 一个字都不说（那颗入口照旧可点）。

## 尺子只有一份

_work/panelsplit/draw_all.py 的 _Layout / _stub_bpy / _Scene / _Settings（与
tests/test_gui_look.py、tests/test_addon_properties.py 同一把，⛔ 不复制）。
本文件只多做一件它不做的事：**画之前把折叠状态摆成指定形状** —— 尺子画的是「默认态」，
量不了「全展开 / 全收起」两种状态。

⚠ 本改动最容易踩的坑正是它：段收起来之后 tests/test_gui_look.py 的 U1–U8
**看不见那些控件了**（AGENTS §0.12.1「尺子看不见要找的东西」）⇒ 见 test_g：
**把全展开的那条序列再走一遍 U1–U8**。

⛔ 本文件不 import 真 bpy（走桩）、不碰游戏目录、不写任何产品文件。
"""
from __future__ import annotations

import functools
import hashlib
import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
ADDON = REPO_ROOT / "blender_addon"
RULER = WORK / "panelsplit" / "draw_all.py"
#: 可选旁证：**产品尺子自己**画出来的那一份（下面的 __main__ 拿它交叉核对；没有就跳过）。
#: 想留一份：.venv/Scripts/python.exe _work/panelsplit/draw_all.py <上面这个路径>
BASELINE = WORK / "panelsplit" / "baseline_panelfold.json"
PKG = "edit_folds_under_test"

#: 产品那份声明的**期望形状**：`(会话键, 抬头)`，顺序 = 面板上从上到下。
EXPECTED_FOLDS: tuple = (
    ("edit_fold_geo", "⌁ 变换段 · 挪整件"),
    ("edit_fold_mat", "⌁ 材质 / 贴图段"),
    ("edit_fold_mymaps", "⌁ 用我的图（法线 / Mask）"),
    ("edit_fold_gamemap", "⌁ 补接游戏原图"),
    ("edit_fold_fx", "⌁ 特效段"),
    ("edit_fold_anim", "⌁ 动画段"),
    ("edit_fold_skins", "⌁ 皮肤 · 贴图（直接换，不落 PNG）"),
    ("edit_fold_mounts", "⌁ 挂点段"),
)
#: 哪几个折叠**挂在另一段里面**（子键 -> 父键）—— 产品那边是 fold_row(mat, ...) 画的。
#: ★ 嵌套卫生：只开子、不开父 ⇒ 那一趟必须与「全收起」**逐条相同**（子折叠不许漏出来）。
FOLD_PARENT = {
    "edit_fold_mymaps": "edit_fold_mat",
    "edit_fold_gamemap": "edit_fold_mat",
}
#: ★ 本轮（task-7）**唯一**打算动的那处 UI 文案：把新名字换回旧的，
#:   必须**逐条**回到上一轮那份指纹（见 test_d —— 这是「只改了该改的」的判据本体）。
RENAMED_FROM = "接游戏原图（VT 抽出来的）"
RENAMED_TO = "补接游戏原图（旧件 / 覆盖用）"
FOLD_KEYS = tuple(key for key, _title in EXPECTED_FOLDS)
DEFAULT_OPEN = ()  # ★ 2026-10-17 用户点名：插件里所有折叠栏默认折叠
#: 每一段的体里**只有它才有**的那样东西 —— 拿它证明「少掉 / 多出来的就是它自己」（⛔ 不串段）。
#: 值是 (条目种类, 比什么)：label 比文案、operator 比 idname（两者都在下标 1）。
SECTION_MARKERS = {
    "edit_fold_geo": ("label", "变换段 · 挪整件（本地矩阵）"),
    "edit_fold_mat": ("label", "材质 / 贴图段"),
    "edit_fold_mymaps": ("operator", "ba_mod.edit_map_bind"),
    "edit_fold_gamemap": ("operator", "ba_mod.edit_map_game"),
    "edit_fold_fx": ("label", "特效段（VFX Graph 改 / 加 / 删）"),
    "edit_fold_anim": ("label", "动画段（看 / 改 / 写回）"),
    #: 皮肤段的**独有**控件：那颗扫描算子（其余段都没有）。
    "edit_fold_skins": ("operator", "bamod.skins_scan"),
    "edit_fold_mounts": ("label", "挂点段（这个单位挂哪个炮塔）"),
}
CONTROL_KINDS = ("label", "prop", "operator", "template_list")

#: ★ 「控件集合逐条不变」的指纹 —— 用本文件量的
#:   （记的是**全展开、剥掉折叠抬头、并把「补接游戏原图」改回旧文案**之后的那条序列）。
#: 重算：.venv\Scripts\python.exe tests/test_edit_folds.py
#:
#: ⚠ **2026-10-17 重记过一次**（task-7 那次的 188 / 90 / d4cb064f… 换成下面这组）。这一轮动了什么：
#:   · **L11 新加「参数族」块**（`panels_edit.py` 动画段：box + 2 label + 2 prop + 2 row + 3 op
#:     = **10 条**）—— 逐条可复算：`.venv\Scripts\python.exe _work/l3/audit_folds.py` 的 `HITS`；
#:   · 另有 **+13 条**来自**同一轮别人的面板改动**（同一条序列里，⛔ L11 这一格没碰它们）——
#:     所以这组数**不是**「L11 一个人加的」，重记时如实写在这里，别当成 L11 的量。
#: 2026-10-17 重记：**所有折叠栏默认收起**（`EDIT_FOLDS_OPEN = ()`）＋ ② 改 新增「⌁ 皮肤 · 贴图」
#: （改走 `fold_row`，样式与其余段一致）＋ 三条读数（包 / 段地图 / 几何段说明）改**单行**
#: ＋ `pc.block` 的「⌁ 动画」等折叠块**也改用 toggle 算子画**（与 fold_row 同一种样式）。
#: ★ 2026-10-18 重记（用户「ABCD 全砍」· slim-edit 改 `panels_edit.py`）：
#:   条目 270 → **201**、控件 97 → **73**、sha `6aaced09…` → `41a6c546…`。
#:   变化的**全部内容**：每段只留「看 / 写回…」＋ 9 颗折叠抬头（原来 53 颗按钮 → 34），
#:   干跑 / 还原 / 只读细看 / 旧 09 动画那一套搬进「看详情 ▸」（同一个 `ba_mod.toggle_detail`）。
#:   抬头 8 组一个没动；`panels_09_anim.py` 一个字节没动（只是现在画在详情里）。
#:   重算：`.venv\Scripts\python.exe tests\test_edit_folds.py`
#: ★ 2026-10-25 重记（块 D2：Blender 面板「② 改」摘掉网格段 —— `mesh_entry.py` 整删、
#:   `panels_edit.py` 的 6 颗网格算子与顶点指纹 timer 一起退役，网格段换成「变换段」四档）。
#:   条目 201 → **207**、控件 73 → **75**、sha 41a6c546… → 4ca60ac9…。
#:   变化的全部内容：少 6 颗网格算子（check/plan/apply/restore/geometry/changed）与两段
#:   顶点专用 prop；多「变换段」四档 `ba_mod.edit_xform` 与一行 `moved_whole()` 读数。
BEFORE_SHA = "4ca60ac91b3a90028d0dd32de2700bbbbc34e47511eb1a76218a37aeab59fc4e"
BEFORE_ITEMS = 207
BEFORE_CONTROLS = 75


@functools.lru_cache(maxsize=1)
def _ruler():
    spec = importlib.util.spec_from_file_location("edit_folds_ruler", RULER)
    assert spec is not None and spec.loader is not None, f"找不到尺子 {RULER}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@functools.lru_cache(maxsize=1)
def _env():
    """影子包 ＋ 与 draw_all.main() 同一套桩（⛔ 不另写一份 bpy 桩 = 不换尺子）。"""
    ruler = _ruler()
    ruler._stub_bpy()
    bpy = sys.modules["bpy"]
    bpy.types.Scene = types.SimpleNamespace()
    scene = ruler._Scene(empty=False)
    bpy.context = types.SimpleNamespace(scene=scene, area=None, window=None)
    sys.path.insert(0, str(REPO_ROOT / "src"))
    package = types.ModuleType(PKG)
    package.__path__ = [str(ADDON)]
    sys.modules[PKG] = package
    importer = types.ModuleType(f"{PKG}.importer")
    importer.list_units = lambda root: ruler.FAKE_KEYS
    importer.unit_hud_names_source = lambda root: ""
    importer.units_stamp = lambda root: "probe"
    importer.catalog_has_address = lambda root, address: False
    importer.unit_names_reader = lambda: (lambda path: {}, "探针")
    build_info = types.ModuleType(f"{PKG}.build_info")
    build_info.stamp = lambda: "（探针）构建戳"
    build_info.stale_note = lambda: ""
    for stub in (importer, build_info):
        sys.modules[stub.__name__] = stub
        setattr(package, stub.__name__.split(".")[-1], stub)
    for name in ("runtime", "materials"):
        stub = types.ModuleType(f"{PKG}.{name}")
        sys.modules[stub.__name__] = stub
        setattr(package, name, stub)
    common = importlib.import_module(f"{PKG}.panels_common")
    edit = importlib.import_module(f"{PKG}.panels_edit")
    for key, _title in common.BLOCKS:  # 折叠块全开（尺子口径：收起 = 尺子看不见）
        setattr(scene.bamod_blocks, key, True)
    common.set_session(**ruler._SESSION_SEED)
    return types.SimpleNamespace(
        scene=scene,
        context=types.SimpleNamespace(scene=scene),
        common=common,
        edit=edit,
        ruler=ruler,
    )


def draw_raw(ctx=None):
    """按**会话里现在记着的**状态真画一遍 ② 改（⛔ 不动折叠状态）。

    `ctx` 给了就用它（造「材质已经接上了」那种现场）；缺省 = 探针那趟（视口里没选东西）。
    """
    env = _env()
    cls = env.edit.VIEW3D_PT_ba_edit
    layout = env.ruler._Layout()
    panel = cls.__new__(cls)
    panel.layout = layout
    panel.draw(env.context if ctx is None else ctx)
    return layout.seq


def draw(folds=None, ctx=None):
    """真画一遍 ② 改。folds = {会话键: 开着吗}；None = 把折叠状态**清空**（默认态）。"""
    env = _env()
    table = dict(env.common.session().get(env.common.DETAIL_KEY) or {})
    for key in FOLD_KEYS:
        table.pop(key, None)
    if folds:
        table.update(folds)
    env.common.set_session(**{env.common.DETAIL_KEY: table})
    return draw_raw(ctx)


def closed_all():
    return draw({key: False for key in FOLD_KEYS})


def open_all():
    return draw({key: True for key in FOLD_KEYS})


def only(key):
    return draw({k: (k == key) for k in FOLD_KEYS})


def default_state():
    """**真机默认态** = 插件上线之后的样子：attach_properties() 把默认值补进会话。

    ⛔ 不能用「会话里没记过」代表默认态 —— 那一条的兜底是**全展开**（见 test_b2），
    而尺子/探针走的正是那条路。
    """
    draw(None)                     # 清掉折叠键（= 这个会话还没种过默认值）
    _env().edit.seed_folds()       # ← register() 走的那一步
    return draw_raw()


def controls(seq):
    """「控件集合」= 真画在屏幕上的那些条目（⛔ 不含 box / row 这些容器标记）。"""
    return [item for item in seq if item[0] in CONTROL_KINDS]


def has_marker(body, marker) -> bool:
    """这一段里有没有那样「只有它才有」的东西（label 的文案 / operator 的 idname）。"""
    kind, want = marker
    return any(item[0] == kind and str(item[1]) == want for item in body)


def fingerprint(seq):
    """那条序列的指纹（逐条、含顺序）。"""
    return hashlib.sha256(json.dumps(seq, ensure_ascii=False).encode("utf-8")).hexdigest()


def _op_prop(seq, index, name):
    """第 index 条算子后面那串属性赋值里的一项（`["op-prop", 名, repr(值)]`）。"""
    found = None
    for item in seq[index + 1:]:
        if item[0] != "op-prop":
            break
        if item[1] == name:
            found = item[2]
    return None if found is None else str(found).strip("'")


def fold_headers(seq):
    """折叠抬头那几组：[(起, 止, 键)] —— 认**产品契约**（共用算子 ＋ 它的 panel 键）。

    ⛔ 不按文案认：文案是 UI，会改；`panel=` 才是契约。
    """
    spans = []
    index = 0
    while index < len(seq):
        item = seq[index]
        if item[0] == "row" and index + 1 < len(seq):
            nxt = seq[index + 1]
            if nxt[0] == "operator" and nxt[1] == "ba_mod.toggle_detail":
                key = _op_prop(seq, index + 1, "panel")
                if key in FOLD_KEYS:
                    stop = index + 1
                    while stop < len(seq) and seq[stop][0] != "/row":
                        stop += 1
                    spans.append((index, stop + 1, key))
                    index = stop + 1
                    continue
        index += 1
    return spans


def strip_fold_headers(seq):
    """剥掉折叠抬头那几组 —— 剩下的必须与**改动前**逐条相同。"""
    out = []
    cursor = 0
    for start, stop, _key in fold_headers(seq):
        out.extend(seq[cursor:start])
        cursor = stop
    out.extend(seq[cursor:])
    return out


def rolled_back_name(seq):
    """把本轮那处改名换回旧文案 —— 换完必须**逐条**等于上一轮那份（见 test_d）。"""
    return [
        [*item[:2], RENAMED_FROM, *item[3:]]
        if item[0] == "operator" and item[1] == "ba_mod.edit_map_game"
        else item
        for item in seq
    ]


def all_open_except(*keys):
    """除这几个键之外**全开** —— 量「关掉某一个只藏它自己那一块」用的基准。"""
    return draw({key: (key not in keys) for key in FOLD_KEYS})


def sole_insertion(base, grown):
    """grown 相对 base **只在某一处多插了一段**时，回 `(起, 那一段)`。

    ⛔ 别处一个字都不许变（顺序变了 / 插了两处都要当场红）—— 这就是
    「折叠只是不画，不是删、不是挪」的判据本体。
    """
    extra = len(grown) - len(base)
    assert extra > 0, f"没有插入：base {len(base)} 条 / grown {len(grown)} 条"
    # ★ 取**最靠左**的那个插入点，判据是两条都在位：
    #   ① 插入点之前逐条相同 ② 插入点之后（各让开 extra 条）逐条相同。
    #   ⚠ 插入点只**定到「撞脸」为止**：边界两边的条目长得一样时（来个 /row 接 /row、
    #   挂点段的 box 与尾巴的 box），边界往左往右挪一条都仍然成立 ⇒ ⛔ 别拿这里的 head
    #   当「体从哪开始」的判据 —— 那个要按**抬头**认（见 section_body）。
    #   （2026-10-16 真踩：拿「第一处不同」当插入点时，挂点段的体被往后挪了一条。）
    for head in range(len(base) + 1):
        if base[:head] == grown[:head] and base[head:] == grown[head + extra:]:
            return head, grown[head:head + extra]
    raise AssertionError(f"不是「只在一处多插了一段」（多了 {extra} 条）")


def without_fold_arrows(seq):
    """把折叠抬头那个三向箭头抹平（TRIA_DOWN / TRIA_RIGHT -> ARROW）。

    ⛔ 不是放松：那个箭头**指示的正是「开 / 关」本身** ⇒ 比较「折叠前后」时必须允许它变；
    抬头那一行**别的**（标题文案 / panel 键 / 它在序列里的位置）一个字都不许变。
    """
    head_ops = {start + 1 for start, _stop, _key in fold_headers(seq)}
    return [
        [item[0], item[1], item[2], "ARROW"] if index in head_ops else item
        for index, item in enumerate(seq)
    ]


def insertion_between(shut_seq, grown_seq):
    """`shut_seq` -> `grown_seq` 多出来的那一段（**原样**，不抹箭头）。

    先按 `without_fold_arrows` 把箭头抹平再比 —— 于是「展开」只该表现为**一处连续插入**。
    """
    head, _normalized = sole_insertion(
        without_fold_arrows(shut_seq), without_fold_arrows(grown_seq)
    )
    width = len(grown_seq) - len(shut_seq)
    return head, grown_seq[head:head + width]


def section_body(shut_seq, grown_seq, key):
    """这一段展开多出来的那一段 —— **从它自己的抬头后面量**（按 panel 键认位置）。

    ⛔ 为什么不能用「两头一比」猜边界：边界会**撞脸**（/row 与 /row、两个 box 一模一样），
    猜出来的边界会左右挪一条。这里认的是 panel= 键，挪不动。

    ★ 并且当场断言：**把这 extra 条拿掉就逐条回到收起态** —— 展开没有动到别处。
    """
    extra = len(grown_seq) - len(shut_seq)
    assert extra > 0, f"{key}: 没有插入（{len(shut_seq)} -> {len(grown_seq)}）"
    spans = {k: (s, e) for s, e, k in fold_headers(grown_seq)}
    assert key in spans, (key, sorted(spans))
    _start, stop = spans[key]
    shut_n = without_fold_arrows(shut_seq)
    grown_n = without_fold_arrows(grown_seq)
    assert shut_n == grown_n[:stop] + grown_n[stop + extra:], (
        f"{key}: 抬头后面那 {extra} 条拿掉之后回不到收起态 —— 展开动到了别处"
    )
    return stop, grown_seq[stop:stop + extra]


# --------------------------------------------------------------------------- #
# 1 · 声明：几段、哪一段默认展开
# --------------------------------------------------------------------------- #
def test_a_seven_folds_declared_and_none_opens_by_default() -> None:
    """产品的声明必须是那**七段**（含两个子折叠），且**一段都不默认展开**。"""
    edit = _env().edit
    declared = tuple((str(key), str(title)) for key, title in edit.EDIT_FOLDS)
    assert declared == EXPECTED_FOLDS, declared
    assert tuple(edit.EDIT_FOLDS_OPEN) == DEFAULT_OPEN, edit.EDIT_FOLDS_OPEN
    assert len(set(FOLD_KEYS)) == len(FOLD_KEYS), FOLD_KEYS


def test_a2_every_fold_title_fits_one_line() -> None:
    """抬头是一行可见文本 ⇒ 显示格 <= TEXT_WIDTH_MAX（视觉规范 L4，量产品那份常数）。"""
    common = _env().common
    over = [
        (title, common.display_width(title))
        for _key, title in EXPECTED_FOLDS
        if common.display_width(title) > common.TEXT_WIDTH_MAX
    ]
    assert over == [], over


# --------------------------------------------------------------------------- #
# 2 · 默认态 = 只开材质 / 贴图段
# --------------------------------------------------------------------------- #
def default_state_problems(seq, want_open, want_seq, shut_seq) -> list:
    """判据本体（纯函数，负控直接喂它）：抬头齐五段 ＋ 与「只开 want_open」相同 ＋ 真开了。"""
    problems = []
    keys = [key for _start, _stop, key in fold_headers(seq)]
    if sorted(keys) != sorted(FOLD_KEYS):
        problems.append(f"折叠抬头不是那七段：{keys}")
    if seq != want_seq:
        problems.append(f"默认态与「只开 {want_open}」画出来的东西不一样")
    if seq == shut_seq:
        problems.append(f"默认态一段都没展开（{want_open} 没开）")
    return problems


def test_a3_the_three_reading_lines_stay_on_one_row() -> None:
    """★ 2026-10-17 用户：「这些地方还是截断的，改成一行」—— 三条读数**各占一行、不折行**。

    判据（能红）：默认态里 `包：` / `段：` / `「几何段」管：` 各出现**恰好一次**，
    且每条显示格 <= `TEXT_WIDTH_MAX`（超了就会折成第二行 —— 正是用户报的那个形状）。
    """
    common = _env().common
    seq = default_state()
    labels = [str(item[1]) for item in seq if item[0] == "label"]
    picks = [text for text in labels if text.startswith(("包：", "段：", "「变换段」管："))]
    assert len(picks) == 3, picks
    for text in picks:
        assert common.display_width(text) <= common.TEXT_WIDTH_MAX, text


def test_b_the_default_state_is_all_folds_collapsed() -> None:
    """★ 用户要的形状（2026-10-17）：真机默认态 = **七段全收起、抬头都在**。"""
    seq = default_state()
    assert seq == closed_all(), "默认态不是全收起 —— 又有人往 EDIT_FOLDS_OPEN 里加了默认展开"
    assert [item for item in seq if item[0] == "operator"], "收拾起可以，抬头不许消失"


def test_b2_without_the_attached_defaults_nothing_is_hidden_from_the_ruler() -> None:
    """★ 与 pc.block 同一条姿势：**没人种默认值 = 全展开**。

    探针 / 尺子不调 attach_properties()，不许因此就看不见那些收起段的控件
    （AGENTS §0.12.1：尺子看不见要找的东西 = 假绿）。
    """
    common, edit = _env().common, _env().edit
    common.set_session(**{common.DETAIL_KEY: {}})
    assert all(edit.fold_open(key) is True for key in FOLD_KEYS)
    assert len(draw_raw()) == len(open_all()), "没种默认值时居然有段被收起来了"


def test_e_the_ruler_can_go_red_when_the_default_group_moves(monkeypatch) -> None:
    """★ 能红（用户点名的那条）：默认组换成别的 / 清空 ⇒ 上面那条判据**必须**报。"""
    edit = _env().edit
    want, want_seq, shut = "edit_fold_mat", only("edit_fold_mat"), closed_all()
    monkeypatch.setattr(edit, "EDIT_FOLDS_OPEN", ("edit_fold_fx",))
    moved = default_state()
    assert moved == only("edit_fold_fx"), "默认组改了，画出来的却还是原来那一段"
    assert default_state_problems(moved, want, want_seq, shut), (
        "默认组换成 特效段 了，判据还是绿的 —— 那条是假绿"
    )
    monkeypatch.setattr(edit, "EDIT_FOLDS_OPEN", ())
    empty = default_state()
    assert empty == shut, "一段都不开时默认态却不是全收起"
    assert default_state_problems(empty, want, want_seq, shut), "一段都不开，判据也必须报"


# --------------------------------------------------------------------------- #
# 3 · 折叠只是不画 —— 不删、不挪、不换名
# --------------------------------------------------------------------------- #
def test_c_closing_one_fold_only_hides_its_own_block() -> None:
    """★ 判据本体（**嵌套也管**）：关掉任何一段 = 只少它自己那一块，别处逐条不变。

    ⚠ 基准取「除它之外全开」而不是「全收起」：子折叠挂在材质段里面，
    父段收着时它连抬头都不画 —— 拿全收起当基准就量不出「关掉它」这件事（量出来是「没变化」）。
    """
    full = open_all()
    stops = []
    for key in FOLD_KEYS:
        shut = all_open_except(key)
        _head, run = insertion_between(shut, full)          # ① 差 = 一处连续插入
        stop, body = section_body(shut, full, key)          # ② 那一段就是它自己的体
        assert body, f"{key} 关掉之后什么都没少（那这一段是空壳）"
        assert len(body) == len(run) == len(full) - len(shut), (key, len(body), len(run))
        assert any(item[0] in ("prop", "operator", "template_list") for item in body), body
        assert has_marker(body, SECTION_MARKERS[key]), (key, body)
        stops.append(stop)
    assert stops == sorted(stops), f"七段的体不是按面板顺序排的：{stops}"
    assert len(set(stops)) == len(stops), f"两段的体撞在一起了：{stops}"


def test_c2_a_child_fold_cannot_leak_out_of_a_closed_parent() -> None:
    """★ 嵌套卫生：父段收着时，子折叠**连抬头都不许画**。

    「只开子、不开父」那一趟必须与「全收起」**逐条相同** —— 否则它就是漏到段外去了。
    """
    shut = closed_all()
    for child, parent in FOLD_PARENT.items():
        assert parent in FOLD_KEYS, (child, parent)
        only_child = draw({key: (key == child) for key in FOLD_KEYS})
        assert only_child == shut, f"{child}: 父段（{parent}）收着，它却冒出来了"
        drawn = [k for _s, _e, k in fold_headers(only_child)]
        assert child not in drawn, (child, drawn)
    # 父段一开，两个子折叠的抬头就都出来了（体还收着 —— 真机默认态就是它），
    # 而且**紧跟**在父段抬头后面（位置就是「画在材质段里」）
    with_parent = draw({key: (key == "edit_fold_mat") for key in FOLD_KEYS})
    keys = [k for _s, _e, k in fold_headers(with_parent)]
    assert keys == list(FOLD_KEYS), keys
    after = keys[keys.index("edit_fold_mat") + 1:][:len(FOLD_PARENT)]
    assert after == list(FOLD_PARENT), keys


def test_c3_the_headers_themselves_never_move_or_rename() -> None:
    """★ 抬头那一行本身从不改：键、文案、条数、相对顺序（只有那个三向箭头会变）。

    父段关掉时它的**子折叠抬头也跟着不画**（那是设计，不是改名）；
    剩下还画着的那些，必须与全展开时**逐条相同**。
    """
    def shape(seq) -> list:
        return [
            (key, str(seq[start + 1][2]), stop - start)
            for start, stop, key in fold_headers(seq)
        ]

    full = shape(open_all())
    assert [item[0] for item in full] == list(FOLD_KEYS), full
    for key in FOLD_KEYS:
        gone = {key} | {child for child, parent in FOLD_PARENT.items() if parent == key}
        left = [item for item in shape(all_open_except(key)) if item[0] not in gone]
        want = [item for item in full if item[0] not in gone]
        assert left == want, (key, left, want)


def test_c4_each_header_sits_right_before_its_own_body() -> None:
    """抬头之后紧接着就是它管的那一块（⛔ 抬头与段不许错位 —— 嵌套也一样）。"""
    full = open_all()
    spans = {key: (start, stop) for start, stop, key in fold_headers(full)}
    assert sorted(spans) == sorted(FOLD_KEYS), sorted(spans)
    for key in FOLD_KEYS:
        _start, stop = spans[key]
        _at, body = section_body(all_open_except(key), full, key)
        assert full[stop:stop + len(body)] == body, f"{key} 抬头后面跟的不是它自己的体"
        assert has_marker(body, SECTION_MARKERS[key]), (key, body)


# --------------------------------------------------------------------------- #
# 4 · 与**改动前**逐条相同（控件一个没多、一个没少）
# --------------------------------------------------------------------------- #
def test_d_this_round_changed_exactly_the_one_rename() -> None:
    """★ 本轮那处改名之外，一个条目都不许动（沿用上一轮那条 sha256 判据）。

    剥掉 **8 组**抬头 ⇒ 201 条 / 73 个控件；再换成旧文案 ⇒ **逐条**等于**本文件头记的那份**
    指纹（41a6c546…，2026-10-18 重记：用户「ABCD 全砍」—— 每段只留「看 / 写回…」，
    干跑 / 还原 / 旧 09 动画那一套进「看详情 ▸」）。
    少一条 / 多一条 / 顺序变了 / 顺手改了别的文案，这条都会红 ——
    这就是「折叠前后控件集合完全相同」在本轮的落点。
    """
    seq = strip_fold_headers(open_all())
    rolled = rolled_back_name(seq)
    got = (fingerprint(rolled), len(rolled), len(controls(rolled)))
    want = (BEFORE_SHA, BEFORE_ITEMS, BEFORE_CONTROLS)
    assert got == want, (
        f"上一轮 {BEFORE_ITEMS} 条 / {BEFORE_CONTROLS} 个控件；本轮改名回滚后 "
        f"{len(rolled)} 条 / {len(controls(rolled))} 个 / {fingerprint(rolled)} —— 重算："
        ".venv/Scripts/python.exe tests/test_edit_folds.py"
    )
    assert len(fold_headers(open_all())) == len(FOLD_KEYS) == 8


def test_d2_the_fingerprint_can_go_red() -> None:
    """★ 正控：那枚指纹不是恒真的 —— 少一条 / 多一条 / 改一条都必须变。"""
    rolled = rolled_back_name(strip_fold_headers(open_all()))
    assert fingerprint(rolled) == BEFORE_SHA
    for spoiler in (rolled[:-1], rolled[1:], [*rolled, ["label", "x", ""]]):
        assert fingerprint(spoiler) != BEFORE_SHA, "指纹对「少一条 / 多一条」都不变 —— 那是假绿"
    assert fingerprint([*rolled[:5], ["label", "x", ""], *rolled[5:]]) != BEFORE_SHA
    # ★ 改名那一层也要能红：**不回滚**就不等于上一轮那份
    assert fingerprint(strip_fold_headers(open_all())) != BEFORE_SHA


# --------------------------------------------------------------------------- #
# 5 · 状态记住 ＋ 复用既有那颗算子 / 那份会话表
# --------------------------------------------------------------------------- #
def test_f_the_fold_state_lives_in_the_shared_session_and_sticks() -> None:
    """★ 折叠状态存**会话**（同一张表、同一颗算子）—— 再画一次不重置。"""
    common, edit = _env().common, _env().edit
    draw({key: False for key in FOLD_KEYS})
    shut = len(draw_raw())      # ⚠ 先量：下面每个 draw 帮手都会重摆折叠状态
    # 全收起时画出来的抬头 = **顶层那几段**（子折叠挂在材质段里 ⇒ 父段收着它不画）
    visible = [k for _s, _e, k in fold_headers(draw_raw())]
    assert visible == [key for key in FOLD_KEYS if key not in FOLD_PARENT], visible
    assert shut == len(closed_all()), "全收起时这几段还在画体"
    common.toggle_detail("edit_fold_geo")          # ← 产品那颗共用算子走的那条路
    assert edit.fold_open("edit_fold_geo") is True
    first, second = len(draw_raw()), len(draw_raw())
    assert first > shut, "翻开了却不画体"
    assert second > shut, "再画一次就缩回去了 —— 状态没记住"
    table = common.session().get(common.DETAIL_KEY) or {}
    assert table.get("edit_fold_geo") is True, table


def test_f2_seeding_writes_the_real_default_and_a_fold_can_still_be_opened() -> None:
    """★ 真机默认（**全部收起**）由 attach_properties() 补进会话：之后点得动、也记得住。"""
    common, edit = _env().common, _env().edit
    common.set_session(**{common.DETAIL_KEY: {}})
    edit.seed_folds()
    table = common.session()[common.DETAIL_KEY]
    want = {key: (key in DEFAULT_OPEN) for key in FOLD_KEYS}
    assert {key: table[key] for key in FOLD_KEYS} == want, table
    assert edit.fold_open("edit_fold_mat") is False, "种了默认值之后那一段还是展开的"
    common.toggle_detail("edit_fold_mat")
    assert edit.fold_open("edit_fold_mat") is True, "点一下展不开"
    edit.seed_folds()
    assert edit.fold_open("edit_fold_mat") is True, "用户点过的状态被 attach 抹回去了"


# --------------------------------------------------------------------------- #
# 5b · task-7：那颗入口**改名 ＋ 降级**；两个子折叠；「已接上」状态
#   用户原话：「既然拿模型，就一并把材质、法线之类的拿了，那这里的接法线什么的，
#   还有意义吗？」⇒ Lead 口径：**不删**，降级 ＋ 分明。
# --------------------------------------------------------------------------- #
def _material_ctx(images, sources):
    """造一个「材质真的接上了」的现场。

    ★ 给的是 material_to_nodes(...)["images_by_role"] 与 user_map_sources(...)
    —— 与产品读数**同一个出处**；⛔ **不给**「选法线 / 选 Mask 那两格」
    （那两格是「打算接哪张」＝ 计划，⛔ 不许拿它冒充状态）。
    """
    env = _env()
    material = types.SimpleNamespace(name="FakeMat")
    obj = types.SimpleNamespace(name="FakeObj", type="MESH", active_material=material)
    mod = sys.modules[f"{PKG}.materials"]
    mod.material_to_nodes = lambda _m, _i=images: {"images_by_role": dict(_i)}
    mod.user_map_sources = lambda _m, _s=sources: dict(_s)
    mod.PLACEHOLDER_PREFIX = "BAMOD占位"
    return types.SimpleNamespace(scene=env.scene, active_object=obj)


def _status_labels(seq) -> list:
    """画出来的那些「已接上」状态行 —— 认产品那个字头，⛔ 不认整句。"""
    prefix = str(_env().edit.GAME_MAP_DONE_PREFIX)
    return [
        str(item[1])
        for item in seq
        if item[0] == "label" and str(item[1]).startswith(prefix)
    ]


def test_i_the_game_entry_is_renamed_and_still_clickable() -> None:
    """① 改名：旧文案**一个都不许留**；新文案**恰好一颗**（⛔ 入口没被删掉）。"""
    edit = _env().edit
    seq = strip_fold_headers(open_all())
    texts = [str(item[2]) for item in seq if item[0] == "operator"]
    assert RENAMED_FROM not in texts, texts
    assert texts.count(RENAMED_TO) == 1, texts
    assert edit.BAMOD_OT_edit_map_game.bl_label == "补接游戏原图"


def test_j_status_only_when_both_maps_are_really_connected() -> None:
    """② 状态：两路都接了**真图** ⇒ 出现「已接上…无需再点」；其余四种一律不许出现。

    ⛔ 四种里最要紧的一条：**占位图不算接上** —— 那正是还要补接游戏的场合。
    """
    good = _material_ctx(
        {"normal": "mine_normal.png", "mask": "mine_mask.png"},
        {"normal": "D:/mine/n.png", "mask": "D:/mine/m.png"},
    )
    labels = [
        str(item[1])
        for item in draw({key: True for key in FOLD_KEYS}, ctx=good)
        if item[0] == "label"
    ]
    hit = [text for text in labels if text.startswith(str(_env().edit.GAME_MAP_DONE_PREFIX))]
    assert hit == ["已接上：法线 / Mask（无需再点）"], (hit, labels)
    # 报的是**实际接的那两张**（⛔ 不是我们打算接的那张）
    assert "法线 ← mine_normal.png" in labels, labels
    assert "Mask ← mine_mask.png" in labels, labels

    for name, images, sources in (
        ("一张都没接", {}, {}),
        ("只接了法线", {"normal": "n.png"}, {"normal": "D:/n.png"}),
        ("只接了 Mask", {"mask": "m.png"}, {"mask": "D:/m.png"}),
        (
            "两路都是占位图",
            {"normal": "P.png", "mask": "P.png"},
            {"normal": "BAMOD占位normal", "mask": "BAMOD占位mask"},
        ),
    ):
        seq = draw({key: True for key in FOLD_KEYS}, ctx=_material_ctx(images, sources))
        assert _status_labels(seq) == [], (name, _status_labels(seq))
        # ⛔ 没接上 = 那颗入口照旧可点（这正是它还有意义的场合）
        assert RENAMED_TO in [str(item[2]) for item in seq if item[0] == "operator"], name


def test_j2_the_status_line_lives_inside_the_game_fold() -> None:
    """③ 那一行状态属于「补接游戏原图」那一格：那一格收起 ⇒ 它也不画（⛔ 不许漏到段外）。"""
    good = _material_ctx(
        {"normal": "mine_normal.png", "mask": "mine_mask.png"},
        {"normal": "D:/mine/n.png", "mask": "D:/mine/m.png"},
    )
    assert _status_labels(draw({key: True for key in FOLD_KEYS}, ctx=good)), "开着时没有状态行"
    closed = draw({key: (key != "edit_fold_gamemap") for key in FOLD_KEYS}, ctx=good)
    assert _status_labels(closed) == [], "那一格收起了，状态行还漏在外面"


def test_j3_the_two_new_folds_hold_exactly_what_the_card_asked_for() -> None:
    """③ 两个子折叠：用我的图（选/强度/占位/三颗接）与 补接游戏原图（那一颗），都默认收起。"""
    edit = _env().edit
    keys = [str(item[0]) for item in edit.EDIT_FOLDS]
    assert "edit_fold_mymaps" in keys and "edit_fold_gamemap" in keys, keys
    assert set(FOLD_PARENT) <= set(keys), FOLD_PARENT
    assert set(edit.EDIT_FOLDS_OPEN) == set(DEFAULT_OPEN) == set()
    both = draw({key: True for key in FOLD_KEYS})
    _at, mine = section_body(all_open_except("edit_fold_mymaps"), both, "edit_fold_mymaps")
    names = [str(item[1]) for item in mine if item[0] == "operator"]
    assert names.count("ba_mod.edit_map_pick") == 2, names
    # ★ 2026-10-18：3 → **2**（「两路都接」搬进了「看详情 ▸」，用户「ABCD 全砍」那一轮）。
    assert names.count("ba_mod.edit_map_bind") == 2, names
    _at, game = section_body(all_open_except("edit_fold_gamemap"), both, "edit_fold_gamemap")
    gnames = [str(item[1]) for item in game if item[0] == "operator"]
    assert gnames == ["ba_mod.edit_map_game"], gnames


# --------------------------------------------------------------------------- #
# 6 · 覆盖：段收起之后 U1–U8 会看不见那些控件 ⇒ 拿全展开那条再走一遍
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=1)
def _look():
    """产品那把视觉尺子（⛔ 不复制它的 U1–U8；只取它的纯函数）。"""
    spec = importlib.util.spec_from_file_location(
        "edit_folds_look", REPO_ROOT / "tests" / "test_gui_look.py"
    )
    assert spec is not None and spec.loader is not None, "找不到 tests/test_gui_look.py"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_g_the_collapsed_sections_are_still_covered_by_the_visual_ruler() -> None:
    """★ AGENTS §0.12.1：收起 = 尺子看不见 ⇒ 把**全展开**那条再喂给 U1–U8。"""
    look = _look()
    seq = open_all()
    problems = {
        "U1 面板名又写了一遍": look.u1_repeats_title(seq, "② 改 · 这个件"),
        "U3 box 没抬头": look.u3_box_problems(seq),
        "U4 一行超宽": look.u4_overwide(seq, look.constants()["TEXT_WIDTH_MAX"]),
        "U5 空行 / 挤三颗": look.u5_row_problems(seq),
        "U6 图标名不在枚举里": look.u6_bad_icons(seq, look.icons()),
        "U7 算子没图标": look.u7_operators_without_icon(seq),
    }
    if look.u8_alignment_missing(seq):
        problems["U8 没设对齐口径"] = True
    bad = {name: value for name, value in problems.items() if value}
    assert bad == {}, bad
    # 用户在屏幕上真正看到的是**默认态** —— 也过一遍（收起之后不该冒出宽行）
    assert look.u4_overwide(default_state(), look.constants()["TEXT_WIDTH_MAX"]) == []


# --------------------------------------------------------------------------- #
# 7 · 尺子自己的正控（⛔ 恒绿的尺子不是尺子）
# --------------------------------------------------------------------------- #
def test_h_the_insertion_ruler_can_go_red() -> None:
    """★ 正控：sole_insertion 对「插两处 / 顺序变了 / 没插」都必须红。"""
    base = [["a"], ["b"], ["c"]]
    assert sole_insertion(base, [["a"], ["X"], ["b"], ["c"]]) == (1, [["X"]])
    assert sole_insertion(base, [["a"], ["b"], ["c"], ["X"]]) == (3, [["X"]])
    # 撞脸的插入点头一条：base=[a,box,b] -> grown=[a,box,X,box,b] 里插的是 [box,X]
    tricky = [["a"], ["box"], ["b"]]
    assert sole_insertion(tricky, [["a"], ["box"], ["X"], ["box"], ["b"]]) == (
        1, [["box"], ["X"]]
    )
    with pytest.raises(AssertionError):
        sole_insertion(base, [["a"], ["X"], ["b"], ["Y"], ["c"]])
    with pytest.raises(AssertionError):
        sole_insertion(base, [["a"], ["c"], ["b"]])
    with pytest.raises(AssertionError):
        sole_insertion(base, base)
    with pytest.raises(AssertionError):
        sole_insertion(base, [["X"], ["Y"], ["a"], ["b"]])
    # ★ 折叠抬头那个**三向箭头**变了不算差别（它指示的就是开 / 关）；标题改了必须算
    shut = [
        ["row"],
        ["operator", "ba_mod.toggle_detail", "⌁ 材质 / 贴图段", "TRIA_RIGHT"],
        ["op-prop", "panel", "'edit_fold_mat'"],
        ["/row"],
        ["label", "尾", ""],
    ]
    body = [["box"], ["label", "体", ""]]
    opened = [*shut[:4], *body, *shut[4:]]
    flipped = [*shut[:1], [*shut[1][:3], "TRIA_DOWN"], *shut[2:4], *body, *shut[4:]]
    assert insertion_between(shut, opened) == (4, body)
    assert insertion_between(shut, flipped) == (4, body)
    renamed = [*shut[:1], [*shut[1][:2], "改了名", shut[1][3]], *shut[2:4], *body, *shut[4:]]
    with pytest.raises(AssertionError):
        insertion_between(shut, renamed)


def test_h2_the_header_ruler_only_counts_the_declared_folds() -> None:
    """★ 正控：抬头只认**产品契约**（共用算子 ＋ panel 键），别的一律不算。"""
    mine = [
        ["row"],
        ["operator", "ba_mod.toggle_detail", "x", "TRIA_DOWN"],
        ["op-prop", "panel", "'edit_fold_mat'"],
        ["/row"],
    ]
    assert fold_headers(mine) == [(0, 4, "edit_fold_mat")]
    # 不在 row 里的不算（那不是一行抬头）
    assert fold_headers(mine[1:]) == []
    # 每一格自己那颗「看详情 ▸」不是折叠段
    other = [
        ["row"],
        ["operator", "ba_mod.toggle_detail", "看详情 ▸", "TRIA_RIGHT"],
        ["op-prop", "panel", "'edit'"],
        ["/row"],
    ]
    assert fold_headers(other) == []
    assert fold_headers([]) == []

if __name__ == "__main__":
    full = open_all()
    shut = closed_all()
    seq = strip_fold_headers(full)
    rolled = rolled_back_name(seq)
    print("折叠键（顺序）:", " ".join(FOLD_KEYS))
    print("抬头（顺序）:", " ".join(key for _s, _e, key in fold_headers(full)))
    print("条数: 全收起", len(shut), "/ 默认态", len(default_state()), "/ 全展开", len(full))
    print("剥掉抬头（本轮）:", len(seq), "条 /", fingerprint(seq))
    print("改名回滚（= 上一轮那份）:", len(rolled), "条 /", fingerprint(rolled))
    print("上一轮记的那份:", BEFORE_ITEMS, "条 /", BEFORE_SHA)
    print("改名回滚 == 上一轮:", (len(rolled), fingerprint(rolled)) == (BEFORE_ITEMS, BEFORE_SHA))
    print("旧文案还在吗:", any(item[2] == RENAMED_FROM for item in seq if item[0] == "operator"))
    print("新文案:", [item[2] for item in seq if item[0] == "operator"
                     and item[1] == "ba_mod.edit_map_game"])
    good = _material_ctx(
        {"normal": "RU_T72B_normal_decoded.png", "mask": "RU_T72B_mask_raw.png"},
        {"normal": "D:/vt/RU_T72B_normal_decoded.png", "mask": "D:/vt/RU_T72B_mask_raw.png"},
    )
    print("已接上状态行:", _status_labels(draw({key: True for key in FOLD_KEYS}, ctx=good)))
    if len(sys.argv) > 1:
        # ★ 独立旁证：拿**产品尺子自己**那趟 JSON 走同一条判据（⛔ 不用本文件画的那条）
        raw = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        ruler = next(p for p in raw["panels"] if p["class"] == "VIEW3D_PT_ba_edit")
        r_rolled = rolled_back_name(strip_fold_headers(list(ruler["seq"])))
        print("产品尺子这趟:", ruler["error"] or "无异常", "/", len(ruler["seq"]), "条 / 抬头",
              len(fold_headers(list(ruler["seq"]))))
        print("尺子那趟 → 剥抬头＋改名回滚:", len(r_rolled), "条 /", fingerprint(r_rolled))
        same = (len(r_rolled), fingerprint(r_rolled)) == (BEFORE_ITEMS, BEFORE_SHA)
        print("== 上一轮那份:", same)
    if BASELINE.is_file():
        report = json.loads(BASELINE.read_text(encoding="utf-8"))
        base = next(p for p in report["panels"] if p["class"] == "VIEW3D_PT_ba_edit")["seq"]
        print("产品尺子基线:", len(base), "条 /", fingerprint(base))
