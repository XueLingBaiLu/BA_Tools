r"""宽度感知（拖宽侧栏不再出 `…`）＋ 内核两行去重（用户 2026-10-17 两条真机反馈）。

## 判据一行

    pytest tests/test_panel_width.py -n 0 -q   -> 全绿
    _work/panelsplit/draw_all.py -> 面板 5 个 · ③ 验 **97 -> 91**（那两行内核不再无条件画）
                                    · edit/install/ledger/take 不变 · 每格 error/header_error 空

★ 2026-10-18（用户「ABCD 全砍」精简）：③ 验 91 -> **28**（四个只读体检收进「体检 ▸」，
  默认收起；展开后 95 条，读数一条不少）。其余四格同轮也降了：② 改 302 -> 233、
  ④ 出 204 -> 195、① 拿 33 / ⑤ 撤 58 不变 —— 见 `_work/panelsplit/slim-final.json`。

## 用户原话

① 「**不要在我完全展开的情况下，显示这些省略号**」—— N 侧栏拖到约 1050 px，`…` 还在
   （根因：`pc.clip` 一律按写死的 40 显示格 = **默认 300 px 侧栏**的基准）。
② 「**我觉得，验证的这一整个板块，与Debug重合了**」—— ③ 验 底部那两行内核读数在
   ⌁ 调试 里已有全量，而且 ①/②/素材盒/配置·起步 里各重复一遍。

## 尺子只有一份（⛔ 本文件不复制它，也⛔ 不改它）

`_work/panelsplit/draw_all.py` 的 `_Layout` / `_Scene` / `_stub_bpy`（走 `_ruler()`）。
⛔ `draw_all.py` 一个字节都不许动（它没有 region ⇒ 必须仍按 40 格 = 现有 u4 判据与指纹不变）。
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

from test_gui_look import _ruler

REPO_ROOT = Path(__file__).resolve().parents[1]
ADDON = REPO_ROOT / "blender_addon"
PKG = "panel_width_probe"
#: 60 显示格的样本（30 个全角字）—— 宽面板要原样画出来，窄面板必须被截。
WIDE = "宽" * 30

_COUNT = 0


def _mount(package, module) -> None:
    sys.modules[module.__name__] = module
    setattr(package, module.__name__.split(".")[-1], module)


def _env(modules: tuple[str, ...] = ("panels_verify", "panels_edit")):
    """影子包（与尺子同一套 bpy 桩）—— 返回现场。"""
    global _COUNT
    _COUNT += 1
    name = f"{PKG}_{_COUNT}"
    ruler = _ruler()
    ruler._stub_bpy()
    bpy = sys.modules["bpy"]
    scene = ruler._Scene(empty=False)
    bpy.context = types.SimpleNamespace(scene=scene, area=None, window=None)
    src = str(REPO_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    package = types.ModuleType(name)
    package.__path__ = [str(ADDON)]
    sys.modules[name] = package
    build_info = types.ModuleType(f"{name}.build_info")
    build_info.stamp = lambda: "v0.0.0 · 探针"
    build_info.stale_note = lambda: ""
    _mount(package, build_info)
    for stub_name in ("importer", "runtime", "materials"):
        _mount(package, types.ModuleType(f"{name}.{stub_name}"))
    common = importlib.import_module(f"{name}.panels_common")
    common.reset_workspace_cache()
    loaded = {}
    for module_name in modules:
        loaded[module_name] = importlib.import_module(f"{name}.{module_name}")
    return types.SimpleNamespace(
        name=name,
        ruler=ruler,
        bpy=bpy,
        scene=scene,
        common=common,
        loaded=loaded,
        context=types.SimpleNamespace(scene=scene),
        layout=ruler._Layout,
    )


def _ctx(env, width) -> types.SimpleNamespace:
    """带 `region.width` 的假 context（`width=None` = **没有** region）。"""
    if width is None:
        return types.SimpleNamespace(scene=env.scene)
    return types.SimpleNamespace(scene=env.scene, region=types.SimpleNamespace(width=width))


def _labels(seq: list) -> list[str]:
    return [str(item[1]) for item in seq if item[0] == "label" and len(item) > 1]


def _draw(env, panel_class, *, header: bool = False) -> list:
    layout = env.layout()
    instance = panel_class.__new__(panel_class)
    instance.layout = layout
    if header:
        panel_class.draw_header(instance, env.context)
        return layout.seq
    if isinstance(panel_class.__dict__.get("draw"), staticmethod):
        panel_class.draw(layout, env.context)
    else:
        instance.draw(env.context)
    return layout.seq


# --------------------------------------------------------------------------- #
# ① 宽度感知
# --------------------------------------------------------------------------- #
def test_wide_sidebar_keeps_sixty_cells_unchanged() -> None:
    """★ 正控：喂 width=1200 ⇒ `draw_limit()` > 40，且一条 **60 格**的 label 原样画出来。"""
    env = _env()
    limit = env.common.set_draw_width(_ctx(env, 1200))
    assert limit > 40, limit
    assert env.common.draw_limit() == limit, (limit, env.common.draw_limit())
    # 60 格 > 40 ⇒ 窄侧栏会截；宽侧栏必须**原样**
    assert env.common.display_width(WIDE) == 60
    assert env.common.clip(WIDE) == WIDE, env.common.clip(WIDE)
    layout = env.layout()
    env.common.label_oneline(layout, WIDE)
    texts = _labels(layout.seq)
    assert texts == [WIDE], texts


def test_narrow_or_unknown_sidebar_still_clips_at_forty() -> None:
    """★ 负控：没有 region / width=0 ⇒ `draw_limit()==40`，同一条 label **必须**被截（带 `…`）。"""
    for width in (None, 0):
        env = _env()
        returned = env.common.set_draw_width(_ctx(env, width))
        assert returned == 0, (width, returned)
        assert env.common.draw_limit() == env.common.TEXT_WIDTH_MAX
        clipped = env.common.clip(WIDE)
        assert clipped.endswith("…"), clipped
        assert env.common.display_width(clipped) <= env.common.TEXT_WIDTH_MAX, clipped
        layout = env.layout()
        env.common.label_oneline(layout, WIDE)
        assert _labels(layout.seq)[0].endswith("…"), _labels(layout.seq)


def test_an_explicit_limit_ignores_the_wide_sidebar() -> None:
    """抬头那条（`limit=18`）**不受宽度影响**：喂 width=1200 仍是 18 格。"""
    env = _env()
    env.common.set_draw_width(_ctx(env, 1200))
    assert env.common.draw_limit() > 40
    long_title = "① 拿 · 游戏里的件（这一句很长很长很长很长很长很长）"
    header = env.common.clip(long_title, 18)
    assert env.common.display_width(header) <= 18, header
    assert header.endswith("…"), header
    # 负控的反面：同一个串在宽面板下**不**用 18 —— 证明上面那条真的在量「显式传的 18」
    assert env.common.display_width(env.common.clip(long_title)) > 18


# --------------------------------------------------------------------------- #
# ② 内核两行去重（只在缺失时一句红话）
# --------------------------------------------------------------------------- #
def _kernel_module(env):
    return importlib.import_module(f"{env.name}._kernel_path")


def test_kernel_warning_is_silent_when_the_kernel_is_fine() -> None:
    """★ 内核正常 ⇒ `kernel_warning()` 空串，③ 验 里**一个字都不画**那两行。"""
    env = _env()
    assert env.common.kernel_warning() == "", env.common.kernel_warning()
    texts = _labels(_draw(env, env.loaded["panels_verify"].VIEW3D_PT_ba_verify))
    assert not [text for text in texts if text.startswith(("内核：", "随包依赖："))], texts


def test_kernel_warning_shows_up_when_the_kernel_is_missing(monkeypatch) -> None:
    """★ 正控（⛔ 不许变成静默）：把 KERNEL_STATUS 换成「内核：**缺失**…」⇒ 必须画出红话。"""
    env = _env()
    missing = "内核：**缺失**（环境里没有、插件也没自带 _kernel/）"
    monkeypatch.setattr(_kernel_module(env), "KERNEL_STATUS", missing)
    warn = env.common.kernel_warning()
    assert warn, "内核缺失时 kernel_warning() 空串 —— 那就是静默"
    assert warn == env.common.KERNEL_MISSING, warn
    texts = _labels(_draw(env, env.loaded["panels_verify"].VIEW3D_PT_ba_verify))
    # ⚠ 40 格窄面板会把这句 53 格的红话截掉尾巴 ⇒ 认**前缀**（宽面板下面原样画）
    assert any(text.startswith("内核缺失") for text in texts), texts


# --------------------------------------------------------------------------- #
# ③ ② 改 的底部空态：已拿件 / 没拿件 各说各的（用户截图：拿过件还写「先『① 拿』」）
# --------------------------------------------------------------------------- #
def _draw_edit(env) -> list[str]:
    edit = env.loaded["panels_edit"]
    return _labels(_draw(env, edit.VIEW3D_PT_ba_edit))


def test_the_empty_state_says_the_right_thing_depending_on_whether_a_piece_is_taken() -> None:
    """★ 已拿件 ⇒ 指向「核对」；没件 ⇒ 才说「先『① 拿』」。⛔ 两句不许同时出现。"""
    env = _env()
    edit = env.loaded["panels_edit"]
    env.common.session().pop(edit.REPORT_KEY, None)  # 没跑过任何核对/写回 ⇒ 走 fallback
    env.common.set_session(**{edit.LAST_KEY: {"unit_key": "RU_T90M/RU_T90M_PKT"}})
    taken = _draw_edit(env)
    assert any("点「核对」" in text for text in taken), taken
    assert not any("先「① 拿」" in text for text in taken), taken
    # 没件那一支
    env.common.set_session(**{edit.LAST_KEY: None})
    empty = _draw_edit(env)
    assert any("先「① 拿」" in text for text in empty), empty
    assert not any("点「核对」" in text for text in empty), empty