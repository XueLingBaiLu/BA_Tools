"""独立对话框（模态/非模态的小窗口）。

| 模块 | 是什么 |
|---|---|
| `dictionary_window` | 字段词典窗口：左选表、右上逐列、右下看取值（只读） |
| `dictionary_data` | 上面那个窗口的数据层（**零 Qt**，可单独断言） |

为什么单开一个包、不继续塞进 `app/shell/`：外壳那一层只负责摆页面与接线
（见 `app/shell/main_window.py` 的模块 docstring），而"表 / 列 / 说明 / 取值"
这套东西既不属于外壳、也不属于某一个页面 —— 它是产品知识的**只读展示**。
数据层与窗口分开，是为了让"有哪些表、怎么过滤、非默认行数怎么算"能在没有
QApplication 的情况下直接被用例断言。
"""

from ba_mod_maker.app.dialogs.dictionary_window import DictionaryWindow

__all__ = ["DictionaryWindow"]
