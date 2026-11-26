"""整个 tests/ 的共用夹具。

三件事：

1. **把保存落点指到临时目录**（原样保留 —— 见 `_autosave_dir` 的说明）；
2. **公共夹具收在这里**：`qapp` / `instance` / `window`，各 GUI 模块不再各抄一份；
3. `window` 的**一个模块只建一次** —— 本轮性能②的核心，见下。

## window 为什么能"一个模块只建一次"

实测（`_work/perf-task6/probe_bind_cost.py`）：

| 建一次什么 | 代价 |
|---|---|
| `MainWindow()` | 42ms |
| `load_dataset(dataset=…)`（全库 24 张表 -> 军械库卡墙） | ~230ms |
| 其中 `ArmoryScreen.set_instance`（399 张卡那面墙） | ~130ms |
| `UnitEditorScreen.set_instance` | ~58ms |
| 国家 / 专精 / 数据表 / 检索 四页 | <30ms |
| **一份干净实例** `load_dataset(root).instance(id)` | **~1ms** |

一条用例真正需要"干净"的只有**数据**（用例会改内存里的行），不是窗口本身。
所以窗口一个模块建一次，每个用例只把**一份新数据**接回去。

## 隔离契约（硬要求）

`window` 每个用例都会：

1. 新建一份干净实例（`instance` 夹具，~1ms）；
2. 把它接回 `_dataset` / `_edit_session` / `_unit_editor` / `_country` / `_spec` /
   `_browser` / `_search_panel`（`bind_window`）；
3. 保存落点换成**这个用例自己的** `tmp_path / "BA_Mod.zip"`；
4. 顶栏回「军械库」页、编辑器里压着的专用页收掉、保存胶囊与保存按钮复位。

唯一**故意**不重接的是军械库那面卡墙（399 张卡，~130ms）：它一直指着模块开头那一份
实例。`window` 的用例**一条都不读卡墙的内容** —— 读卡墙的是军械库自己的模块
（`test_armory_screen.py`，它用独立的 `screen` 夹具）。真要"卡墙也跟着这份新数据"，
显式用 `fresh_armory_window`。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

import pytest

# --------------------------------------------------------------------------- #
# 按路径加载 `blender_addon/` 的子模块（★ 2026-10-18 整改后**必须这样加载**）
# --------------------------------------------------------------------------- #
#: 合成包名（只是 sys.modules 里的一把钥匙，不对应磁盘上的包）。
ADDON_PKG = "bamod_addon_under_test"


def addon_package() -> types.ModuleType:
    """造一个**只有 `__path__` 的空包**，指向 `blender_addon/`（幂等）。

    为什么需要它（两条都真踩过）：
    1. `blender_addon/__init__.py` 顶层 `import bpy` ⇒ 用例**不能** `import blender_addon`；
    2. 但子模块之间有 `from . import session` / `from . import unit_graph` ⇒ 拿裸模块名
       按路径加载会 `attempted relative import with no known parent package`。
    解：空包只提供 `__path__`，不执行 `__init__`，于是相对 import 解析得动、bpy 也不被拉进来。
    """
    package = sys.modules.get(ADDON_PKG)
    if package is None:
        package = types.ModuleType(ADDON_PKG)
        package.__path__ = [str(Path(__file__).resolve().parent.parent / "blender_addon")]  # type: ignore[attr-defined]
        sys.modules[ADDON_PKG] = package
    return package


def load_addon_module(name: str, *, as_name: str | None = None) -> types.ModuleType:
    """按 `<合成包>.<name>` 加载一个 addon 子模块；`as_name` 可另给一个 sys.modules 键。

    :param name: `blender_addon/` 下的模块名（不带 `.py`）。
    :param as_name: 历史用例按某个名字注册时给（例 `l2_xform_entry`；
        原先举的例子是 `l2_mesh_entry`，那个面板 2026-10-25 随 mesh 栈退役）。
    """
    import importlib.util

    package = addon_package()
    full = f"{ADDON_PKG}.{name}"
    cached = sys.modules.get(full)
    if cached is not None:
        return cached
    path = Path(package.__path__[0]) / f"{name}.py"  # type: ignore[attr-defined]
    spec = importlib.util.spec_from_file_location(full, path)
    assert spec is not None and spec.loader is not None, f"加载不了 {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    setattr(package, name, module)
    if as_name:
        sys.modules[as_name] = module
    spec.loader.exec_module(module)
    return module

#: GUI 用例统一用的那个实例（与各模块原先那行 `INSTANCE_ID` 一致）。
#:
#: ⚠️ **数据源换版本后 pathID 会变**（2026-11-11 实测：`DataBaseCompiled` 顶层只剩
#: 52713 / 52714 / 52715 / 52716 / 52739，52085 已随旧版挪进 `_archive-1.2.1`）——
#: 写死一个号的结果是**每条用例都在 setup 里 KeyError**，那不是用例红，是尺子过期。
#: 所以：优先用这个历史号，缺了就取数据源自己列出来的最后一个（读数见 clean_instance）。
INSTANCE_ID = "52085"

#: 每条用例收尾时，进程里允许存在的**顶层控件**上限。
#:
#: 顶层控件数是个**体检指标**：健康的测试进程里它是个常数（实测 9~20，
#: 跟跑了多少条用例无关）。它一旦随用例数单调上涨，就说明有人建了无父控件
#: 却没销毁 —— 而 apply_theme 之类 O(控件数) 的操作会跟着越来越慢，
#: 那正是"测试越跑越慢"的全部机制（下一条夹具的 docstring 里有实测数字）。
#:
#: 所以这里不写成"建议"，写成**上限**：涨上去当场红，而不是慢慢变慢。
#: 真要临时放宽，用 BA_MM_TOP_LEVEL_BUDGET=<n>。
TOP_LEVEL_WIDGET_BUDGET = int(os.environ.get("BA_MM_TOP_LEVEL_BUDGET", "64"))


def pytest_addoption(parser):
    parser.addoption(
        "--all",
        action="store_true",
        default=False,
        help='连 slow 一起跑（撤销 addopts 里那句 -m "not slow"）',
    )


def pytest_configure(config):
    """--all = 全量。

    为什么要有这个开关：addopts 里默认带 -m "not slow"（日常档），而**在 PowerShell 里
    表达"全量"很别扭** —— `pytest -m ""` 会被 PowerShell 吞掉那个空参数，
    `-m "slow or not slow"` 又没人记得住。写成 `pytest --all` 就没有歧义。

    ⚠️ 它会把 -m 一起清掉：要只跑慢档，用 `pytest -m slow`（不加 --all）。
    """
    if config.getoption("--all", False):
        config.option.markexpr = ""


# --------------------------------------------------------------------------- #
# 临时目录落点：⛔ 不许回落到仓根（2026-10-19 定）
# --------------------------------------------------------------------------- #
#: 实测（2026-10-19）：本机 '%TEMP%' 对 'python.exe' **不可写**（mkdtemp 报 WinError 5 后
#: 走候选表）⇒ 'tempfile.mkdtemp()' 落到最后一项 '"."' = 进程 cwd = **仓根**。
#: 于是每跑一次 pytest，仓根就多几十个 'gui_rules_xxx/' / 'gui_look_empty_xxx/'。
#: 6 天攒了 **1,561 个**（'git status' 的未跟踪条目一半是它们）—— 清理时已删。
#: 解：把 'tempfile.tempdir' 与子进程的 'TMP'/'TEMP' 都指到仓内 '.tmp/'（已进 .gitignore），
#: 会话收工再删掉。
SCRATCH_TMP = Path(__file__).resolve().parents[1] / ".tmp"


def _point_tempdir_at_scratch() -> Path:
    SCRATCH_TMP.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(SCRATCH_TMP)
    os.environ["TMP"] = os.environ["TEMP"] = str(SCRATCH_TMP)
    return SCRATCH_TMP


_point_tempdir_at_scratch()


def pytest_sessionfinish(session, exitstatus):
    """收工把 '.tmp/' 清掉（xdist 下只在控制器进程里跑一次）。"""
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    shutil.rmtree(SCRATCH_TMP, ignore_errors=True)


@pytest.fixture(autouse=True, scope="session")
def _autosave_dir(tmp_path_factory):
    """把编辑器的自动保存落点指到本次测试的临时目录。

    产品侧那条接缝是 `BA_MOD_MAKER_SAVE_DIR`（与 BA_MOD_MAKER_GAME 同一类）：
    不指的话每跑一次测试就往工作区根目录写一个真的 BA_Mod.zip。
    """
    target = tmp_path_factory.mktemp("autosave")
    previous = os.environ.get("BA_MOD_MAKER_SAVE_DIR")
    os.environ["BA_MOD_MAKER_SAVE_DIR"] = str(target)
    yield target
    if previous is None:
        os.environ.pop("BA_MOD_MAKER_SAVE_DIR", None)
    else:
        os.environ["BA_MOD_MAKER_SAVE_DIR"] = previous


@pytest.fixture(autouse=True)
def _flush_deferred_deletes():
    """每条用例收尾时，把 detach() 排下的销毁**真的**跑掉。

    ## 为什么必须有这一条

    QApplication.processEvents() **不处理 DeferredDelete**。实测
    （探针 _work/probe_widget_leak.py）：

        建 50 个 hide() + setParent(None) + deleteLater() 的控件
        -> 经 processEvents()                          allWidgets 还是 50（一个没少）
        -> 再 sendPostedEvents(None, DeferredDelete)   allWidgets 变 0

    而产品侧 app/widgets/detach.py 正是"先 hide、再 setParent(None)、最后 deleteLater"。
    不冲这一下，那些控件从此**没有父**（= 顶层控件）却一直活着。

    ## 不冲的代价（实测，这就是"测试越跑越慢"的根）

    test_ui_redesign 一个模块攒到 **6099 个控件 / 3800+ 个顶层控件**（起点约 200），
    而 apply_theme(app) 的代价是 O(活着控件数)（实测 ~55us/控件，线性）。于是同一条用例：

        test_inline_editor_carries_no_doc    单跑 3.39s -> 跟模块跑到第 40 条 21.30s
        test_hover_lights_exactly_one_row    单跑 0.51s -> 跟模块跑到第 23 条 18.38s

    （-n 8 会把这个累积摊薄，所以"并行跑得快"**掩盖**了它 —— 别因为绿了就以为没事。）

    产品里不会有这个问题：事件循环一直在转，DeferredDelete 正常处理。
    这是**测试夹具自己的漏**，所以修在夹具里，不动产品代码。
    """
    yield
    if os.environ.get("BA_MM_NO_FLUSH"):
        # 逃生口：怀疑"冲销毁"本身干扰了某条用例时，用它把这一条关掉再复现一次。
        return
    if "PySide6.QtWidgets" not in sys.modules:
        # 纯 core/ 模块一个 Qt 符号都没碰过 —— 不为它把 PySide6 导进来。
        return
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    # ---- 钉子：不许再涨回去 ----
    tops = len(app.topLevelWidgets())
    if tops > TOP_LEVEL_WIDGET_BUDGET:
        raise AssertionError(
            f"顶层控件涨到 {tops} 个（上限 {TOP_LEVEL_WIDGET_BUDGET}）——"
            "有测试建了**没有父**的控件却没销毁。"
            "这正是「测试越跑越慢」的根：apply_theme 之类的操作是 O(活着控件数)，"
            "控件攒起来之后同一条用例能从 0.5s 变 18s。"
            "修法：建了就 deleteLater()（产品侧的 app/widgets/detach.py 就是这个模式），"
            "别指望 processEvents() —— 它不处理 DeferredDelete。"
        )


@pytest.fixture(autouse=True, scope="module")
def _icon_threads_do_not_outlive_the_module():
    """模块收尾：把还活着的图标加载线程**全部**停掉。

    ## 为什么这不是洁癖，是防崩溃

    每建一个 IconCache 就起一条 _Loader 线程；线程里的 resolver 会按住最多两个
    已打开的 bundle（立绘包 304 MB、dlc_3 724 MB）。而 UnityPy 的不安全是
    **进程级**的（类型树缓存、fsspec 的 fs 实例都是全局的）—— 线程一多就在
    BundleFile.read_fs / parse_preload_table 里撞出
    "Windows fatal exception: access violation"。
    产品侧两处注释早就记着这件事：theme/icons.py 的 shared_icon_cache()、
    core/assets/resolver.py 的 _RESOLVE_LOCK。

    ## 实测（本轮，-n 4，全部在 HEAD 上复现过）

        tests/test_shell_tasks.py + test_ui_redesign.py + test_shell_menu.py + test_ammo_block.py
        跑 5 次：带本夹具前崩 1 次；把守卫关掉（BA_MM_NO_FLUSH=1）再跑，前 2 次就崩 1 次
        —— 崩的是 **test_shell_tasks**，它一个图标都不碰，纯粹是被上一条用例留下的
        线程害的。崩溃点在 C++ 里、Python 栈是空的，所以现象是"随机红/随机 worker 崩"。

    这个夹具就是断掉这条传染链：模块一结束就把进程里所有 IconCache 关掉。
    产品侧不用改：全程只有一条加载线程，进程退出时线程自己结束。
    """
    yield
    if "ba_mod_maker.app.theme.icons" not in sys.modules:
        # 这个模块一条图标线程都没起过 —— 连 gc 都不用跑。
        return
    import gc

    from ba_mod_maker.app.theme import reset_shared_icon_cache
    from ba_mod_maker.app.theme.icons import IconCache

    for obj in gc.get_objects():
        if isinstance(obj, IconCache):
            obj.shutdown()
    reset_shared_icon_cache()


# --------------------------------------------------------------------------- #
# 公共夹具
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def qapp():
    """整个会话一个 QApplication。

    不 mock 控件：这一层的用例要验的正是"控件和数据接对了没有"。
    无字体的平台（offscreen）直接 skip —— 与 test_theme 同一策略：
    宁可不给结论，也不给假结论。
    """
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    try:
        app = QApplication.instance() or QApplication([])
    except Exception as exc:  # pragma: no cover - 依赖运行环境
        pytest.skip(f"无法创建 QApplication：{exc}")
    if not QFontDatabase.families():
        pytest.skip("该平台没有可用字体，控件度量不可信")
    return app


def settle(times: int = 3) -> None:
    """把「排到下一轮事件循环」的刷新跑掉。

    ❗ 这是**契约**，不是测试将就实现：改动之后「重排左栏 + 重绘卡片」是异步的
    （unit_editor_screen._schedule_refresh）—— 目的就是让浮层立刻消失、不卡那 40ms。
    所以用例在断言「界面跟着变了」之前，必须先把那一轮跑掉。
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for _ in range(times):
        app.processEvents()


def clean_instance():
    """从磁盘读一份**干净实例**（实测 ~1ms）。

    为什么这么便宜还有必要每次新读：`instance()` 只解一个实例的 24 张表，
    而用例会直接改 `Table.rows` —— 同一份实例跨用例就是串数据。
    """
    from ba_mod_maker.core.data import find_dataset_root, load_dataset

    root = find_dataset_root()
    if root is None:
        pytest.skip("未找到 DataBaseCompiled 数据源")
    dataset = load_dataset(root)
    # ❗ 号不存在就退到最后一个（见 INSTANCE_ID 的注释）—— 不让整批用例死在 setup 里。
    path_id = INSTANCE_ID if INSTANCE_ID in dataset.instances else list(dataset.instances)[-1]
    return dataset.instance(path_id)


def dataset_with(instance):
    """把一份现成实例装回 Dataset（省掉再读一遍磁盘）。"""
    from ba_mod_maker.core.data.dataset import Dataset

    return Dataset(
        root=instance.directory.parent,
        schema=instance.schema,
        instances={instance.path_id: instance},
        instance_roles={instance.path_id: "default_non_campaign"},
    )


@pytest.fixture()
def instance():
    """每个用例一份新实例：用例会改内存里的行，数据不许跨用例串。"""
    return clean_instance()


@pytest.fixture(scope="module")
def shared_instance():
    """一个模块共用一份实例 —— **只给"全篇只读"的模块用**（例：军械库那面卡墙）。

    换来的是列索引 / `CardData` 缓存**热着**（实测 test_armory_screen 整模块省 6s），
    代价是模块内不再"每例一份"。用它的模块必须自己保证用例不改数据
    （或改完自己收拾干净）—— 见 `test_armory_screen.instance` 的说明。
    """
    return clean_instance()


# --------------------------------------------------------------------------- #
# 窗口：一个模块一个，用例只换数据
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def module_window(qapp):
    """**一个模块只建一次**的主窗口（`MainWindow()` + 一次 `load_dataset`）。

    用例拿到的应当是 `window`（它在这个窗口上换数据），不是它本身。
    """
    from ba_mod_maker.app.shell.main_window import MainWindow

    win = MainWindow()
    win.load_dataset(dataset=dataset_with(clean_instance()))
    yield win
    win.close()
    win.deleteLater()


@pytest.fixture()
def window(module_window, instance, tmp_path):
    """把这一份干净数据接回那个**共用**窗口（不重建卡墙）。"""
    import os as _os

    if _os.environ.get("BA_MM_TRACE_WIDGETS"):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtWidgets import QWidget as _QWidget

        where = _os.environ.get("PYTEST_CURRENT_TEST", "?").split("::")[-1]
        print(
            f"[widgets] {where}"
            f" start={len(module_window.findChildren(_QWidget))}"
            f" tops={len(QApplication.topLevelWidgets())}",
            flush=True,
        )
    bind_window(module_window, instance, tmp_path / "BA_Mod.zip")
    yield module_window
    if _os.environ.get("BA_MM_TRACE_WIDGETS"):
        from PySide6.QtWidgets import QWidget as _QWidget

        print(f"[widgets] end={len(module_window.findChildren(_QWidget))}", flush=True)


@pytest.fixture()
def fresh_armory_window(module_window, instance, tmp_path):
    """同 `window`，但**连军械库那面卡墙也重接到这份数据**（多花 ~130ms）。

    只有"改了行、然后要卡墙跟着变"的用例才需要它。
    """
    bind_window(module_window, instance, tmp_path / "BA_Mod.zip")
    module_window._armory.set_instance(instance)
    return module_window


def bind_window(win, instance, save_path) -> None:
    """把一份新实例接回一个**已经在跑**的窗口。

    这就是 `MainWindow._activate_instance` 减去军械库那面卡墙 —— 外加三件
    "新窗口才有"的事情：保存落点、顶栏回军械库页、保存胶囊/按钮复位。
    """
    from ba_mod_maker.app import diag
    from ba_mod_maker.app.shell.main_window import PAGE_ARMORY

    _drop_editor_pages(win)

    win._dataset = dataset_with(instance)
    win._show_all_instances = False
    win._instance = instance
    win._edit_session.set_instance(instance)
    win._unit_editor.set_instance(instance)
    win._country.set_instance(instance)
    win._spec.set_instance(instance)
    win._browser.set_instance(instance)
    win._search_panel.set_instance(instance)
    win._instance_label.setText(f"实例：{instance.path_id}")
    win._sync_instance_menu()
    diag.set_context(instance=instance.path_id)
    _reset_editor_view(win)

    # ❗ 新实例 = 新的空变更集，但 `set_instance` **不发 changed 信号** ——
    #    不主动同步的话，上一个用例留下的"保存按钮亮着 / 胶囊写着 N 处未保存"
    #    会带进下一个用例（`test_save_pill_and_button_follow_the_cache` 一进来就断言
    #    "刚打开、没改动 -> 灭的"）。
    win._edit_session.set_save_path(save_path)
    win._save_pill.mark_dirty("● 未改动", tip="改任何一格都只进缓存；点「💾 保存」才写进数据库")
    win._sync_save_state()

    win.show_page(PAGE_ARMORY)


def _reset_editor_view(win) -> None:
    """复位编辑器里"用户走到哪儿了"那点**视图状态**。

    `set_instance` 只换数据，**不清**这些 —— 它们是跨用例留着的：

    - 关系树的展开态 / 选中行 / 抬头（`_expanded` / `_selected_key`）；
    - 右栏现在看的是哪一行（`_focus_ref`）。

    漏了它的实测症状（Lead 报的）：`test_relation_tree.py::test_the_tree_starts_collapsed`
    —— "第一次进编辑器，十个分组全收起" —— 跟着整个模块跑会红（上一条展开过那棵树），
    单跑就绿。同理，任何断言"刚进编辑器面包屑是 军械库 › 单位名 › 单位"、
    "右栏是这个单位的 31 条"的用例，都会被上一条留下的 `_focus_ref` 改掉结果。

    这两个动作就是"新窗口本来是什么样"：树面板构造时 `clear()` 过一次，
    新页面的 `_focus_ref` 是 None。
    """
    main = win._unit_editor.main_page
    main._focus_ref = None
    main.tree.clear()
    # 编成台那一页有自己的"选中了哪一行"（右栏画的就是它）—— 没建过就别去建
    # （建它 = 要 import 三块中栏，见 UnitEditorScreen._ensure_bench）。
    if win._unit_editor.bench_ready:
        win._unit_editor.bench_page().reset_view()


def _drop_editor_pages(win) -> None:
    """把编辑器栈里压着的专用页收掉（它们拎着上一个用例的那份 context）。"""
    editor = win._unit_editor
    stack = editor._stack
    stack.setCurrentIndex(0)  # 0 = 主编辑页（见 unit_editor_screen.PAGE_MAIN）
    # ❗ **顶层页要留着**：0 = 关系树那一页，编成台是与它并列的第二个顶层页
    #    （下一条用例可能正落在编成台上）。这里只收掉 _push 上来的专用页。
    keep = {stack.widget(0)}
    if editor.bench_ready:
        keep.add(editor.bench_page())
    for index in range(stack.count() - 1, 0, -1):
        page = stack.widget(index)
        if page in keep:
            continue
        stack.removeWidget(page)
        page.deleteLater()
