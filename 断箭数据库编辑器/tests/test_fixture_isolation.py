"""模块级窗口的**隔离契约**（性能② task-6 的验收项之一）。

`window` 从"每个用例建一个窗口"改成"一个模块建一个窗口、每个用例只把一份干净数据
接回去"之后，最容易坏的一条就是**用例之间串数据**：上一条改的行 / 落盘 / 顶栏状态
漏到下一条。这个文件专门守它 —— 它不是"我觉得应该没事"，是**主动证明**：

- ① 改一行、写盘到自己那份包，窗口里看得见；
- ② **紧接着**换一份干净实例（= 下一个用例），断言它看不到上一条的任何改动：
  数据是干净的、缓存是空的、保存按钮是灭的、顶栏回到军械库页。

## ❗ 为什么是**一条**用例（2026-10-02 实测后合并）

原来拆成 `test_a_...` / `test_b_...` 两条，靠"pytest 按文件内声明顺序、同一个进程执行"
这个前提，用**模块全局变量**把 ① 的读数传给 ②。但 `addopts` 是
`-n auto --dist worksteal` —— **它不保证同一文件的两条落在同一个 worker 上**：

    实测 pytest tests/test_fixture_isolation.py -n 2   -> "1 failed, 1 passed"
         (test_a 在 gw0 设了 _A_TARGET，test_b 在 gw1 读到的还是 None)
    实测默认档（-n auto）连跑 3 次                       -> 3/3 红
    实测 -n 0（串行）                                   -> 绿

⛔ `pytest.mark.xdist_group` **在 worksteal 下是空操作**（本轮实测：加了照红）。
查过 `.venv/Lib/site-packages/xdist/scheduler/worksteal.py`：里面**一个 group 都没有**，
那个 mark 只有 `--dist loadgroup` 认（pytest-xdist 3.8.0）。

也**不能**只把两条并成一条就完事 —— 中间那次"按用例重绑干净实例"是 function 级夹具做的，
并起来那一步就不发生了，等于把这条契约**测没了**。

所以这里的做法是：**一条用例，但显式走两次"换干净实例"**，用的是 `conftest.bind_window`
—— 那正是 `window` 夹具在两条用例之间**调用的同一个函数**。守的还是同一条契约，
只是不再依赖 worker 调度。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.data import find_dataset_root

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

#: 用设计稿那个样本单位（BTR-82A），与 test_unit_editor 同一条。
UNIT = 1
TABLE = "Units"
COLUMN = "Cost"


@pytest.fixture()
def ref():
    from ba_mod_maker.core.edit.refs import CellRef

    return CellRef(TABLE, UNIT, COLUMN)


def test_a_case_boundary_carries_nothing_over(window, module_window, ref, tmp_path) -> None:
    """① 改一行 -> 窗口里看得见 -> 写盘落到**这个用例的** tmp_path；
    ② 再换一份干净实例（= 下一个用例）-> **什么都看不到**。

    ② 那一段走的是 `conftest.bind_window` —— `window` 夹具在两条用例之间做的同一件事。
    """
    from ba_mod_maker.app.shell.main_window import PAGE_ARMORY
    from conftest import bind_window, clean_instance

    # ---------------------------------------------------------------- ①
    window._open_unit_editor(UNIT)
    editor = window._unit_editor
    mine = tmp_path / "BA_Mod.zip"
    editor.set_save_path(mine)

    before = editor.context.cell(ref).raw
    mutated = int(before) + 7777
    editor.apply_cell(ref, mutated)

    assert editor.context.cell(ref).raw == mutated, "改动没进窗口的 context"
    assert editor.pending_count == 1, "改动该在缓存里"

    editor.save()
    assert mine.is_file(), "点了保存却没落到这个用例的 tmp_path"

    # ---------------------------------------------------------------- ②
    # "下一个用例"：换一份**新读的**干净实例，并把落点也换掉（= window 夹具做的事）。
    nxt_dir = tmp_path / "next"
    nxt_dir.mkdir(exist_ok=True)
    nxt = nxt_dir / "BA_Mod.zip"
    bind_window(module_window, clean_instance(), nxt)

    # 顶栏与缓存：新窗口才有"刚打开、没改动"的样子。
    assert window._tabs.currentIndex() == PAGE_ARMORY, "上一个用例把页面留在别处了"
    assert not window._save_button.isEnabled(), "上一个用例的改动把保存按钮留亮了"
    assert window._edit_session.change_count == 0, "缓存里的改动串到下一个用例了"
    assert window._edit_session.last_saved_path is None, "上一个用例的落盘回执串过来了"

    # 数据：拿到的是**这一份**干净实例，不是上一条改过那份。
    window._open_unit_editor(UNIT)
    editor2 = window._unit_editor
    raw = editor2.context.cell(ref).raw
    assert raw != mutated, f"上一条把 {TABLE}.{COLUMN} 改成了 {mutated}，这一条还看得见"
    assert editor2.context.instance is window._instance, "编辑器接的不是这一条的数据"

    # 落点：换成这一条自己的，写盘不会落到上一条那儿去。
    editor2.apply_cell(ref, int(raw) + 1)
    editor2.save()
    assert nxt.is_file(), "写盘没落到这一条自己的落点"
    assert nxt != mine
