"""仓根 conftest：让 pytest 的 **xdist worker** 在这个工作区能起来（⛔ 别删）。

## 它修的是什么（2026-10-08 实测，Lead 记录）

工作区路径是 `D:\\断箭模组制作\\…`（非 ASCII），而控制台代码页是 **936（GBK）**。
`execnet` 启 worker 时会在**子进程的文本 stdin** 上读一行引导串
（`python -u -c "import sys;exec(eval(sys.stdin.readline()))"`）——那一步用 cp936 解 UTF-8 字节，
于是 worker 当场死于：

    UnicodeEncodeError: 'utf-8' codec can't encode character '\\udcad' ... surrogates not allowed

症状是 `pytest`（addopts 默认 `-n auto`）在 **sessionstart** 就报
`INTERNALERROR … EOFError: expected 1 bytes, got 0`，一条用例都跑不了；`-n 0` 串行正常。
两条负控/正控读数（都在本仓实测）：

    \\.venv\\Scripts\\python.exe -c "import execnet; execnet.makegateway()"
        -> 裸探针也炸 ⇒ 不是 pytest 的锅
    $env:PYTHONIOENCODING='utf-8'; pytest -n 4 tests/test_version.py -q
        -> 4 passed ⇒ 修在这里

## 为什么必须放**仓根**（不能放 tests/conftest.py）

worker 在 `pytest_sessionstart` 就起来了，而 `tests/conftest.py` 要等**收集**阶段才导入 ⇒ 太晚。
不带路径跑 `pytest` 时（走 `testpaths`），初始 conftest 只从**仓根**取 —— 所以就是这里。

## 口径

只设 `PYTHONIOENCODING=utf-8`（**stdio 编码**，正好是出问题的那一处）；
更粗的那把锤子是 `PYTHONUTF8=1`（实测同样能修，但它会连 `open()`/文件系统的默认编码一起改，
没必要）。两条都用 `setdefault`：**用户自己设了就以用户的为准**。
"""

from __future__ import annotations

import os

#: worker 的文本 stdio 必须按 UTF-8 解 —— 见模块头那两条复算命令。
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
