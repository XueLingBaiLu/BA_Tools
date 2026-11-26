"""`diag` 子命令：一行体检 ＋ 环境 ＋ 最近事件（Debug 模式，产物能整段粘给助手）。

## 为什么命令行也要有一个

界面上有「🐞 调试…」（`app/diag/ui.py` 的 DebugDialog），但**界面起不来**的时候
（或者根本不想开界面）也要能拿到同一段文字。两边拼的是**同一份**纯函数
（`app/diag/report.py`）—— ⛔ 不许各写一套，否则"用户粘过来的那份"和"我这边看到的"
可能根本不是同一段东西。

## 开关

| 开关 | 效果 |
|---|---|
| （默认） | 人读：一行体检 ＋ 环境 ＋ 最近事件 |
| `--json` | stdout 只有一个 JSON 文档，人读那份改走 stderr（见 `cli/emit.py`） |
| `--tail N` | 日志尾巴带几行（默认 40，`0` = 不带） |
| `--out PATH` | 另存一份到**指定文件**（父目录不存在就建；报告真实路径） |

## 退出码

| 码 | 什么时候 |
|---|---|
| 0 | 读完了（哪怕日志还没有 / 读不动，那也是**一段有用的读数**，不是失败） |
| 2 | `--out` 指到了写不进去的地方 |

## 唯一一条纪律：⛔ 模块级不 import 产品里的东西

`cli/__main__.py` 在 `--help` 时就会 import 本模块。产品里那边 `app/diag/__init__.py`
会把 `log.py`（模块级 import PySide6）一起拉起来 —— 所以 `report` 只在 `run()` 里**按需**
import。判据：`python -c "from ba_mod_maker.cli import __main__; __main__._build_parser()"`
跑完之后 `'PySide6' in sys.modules` 必须是 False（`tests/test_diag_report.py` 钉着）。
"""

from __future__ import annotations

import argparse
import platform
from pathlib import Path
from typing import Any

from ba_mod_maker.cli.emit import Emitter

#: 退出码。`__main__.py` 的帮助文本只说"2 = 用不了"，具体分档在这里。
EXIT_OK = 0
EXIT_UNWRITABLE = 2

#: `--tail` 的默认值。
#: ⚠️ 必须等于 `app/diag/report.py::DEFAULT_TAIL`；模块级 import 那个模块会把 Qt 拉进
#: `--help`（见模块头），所以这里抄一份数 —— `tests/test_diag_report.py::
#: test_tail_default_matches_the_report_module` 钉住两边不许漂移。
TAIL_DEFAULT = 40

_DESCRIPTION = """一行体检 ＋ 环境 ＋ 最近事件（Debug 模式：把这段整段粘给助手就够定位）。

退出码：
  0  读完了（日志还没有 / 读不动也是一段有用的读数，不算失败）
  2  --out 指到了写不进去的地方
"""

_EPILOG = r"""例：
  ba-mod-maker diag
  ba-mod-maker diag --tail 200
  ba-mod-maker diag --json
  ba-mod-maker diag --out D:\某处\diag.txt
"""


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """注册 `diag` 子命令（`__main__.py` 只调这一句）。"""
    parser = sub.add_parser(
        "diag",
        help="一行体检 ＋ 环境 ＋ 最近事件（Debug 模式；--out 落盘，--json 出文档）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="只输出一个 JSON 文档（人读那份改走 stderr）",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=TAIL_DEFAULT,
        metavar="N",
        help=f"日志尾部带几行（默认 {TAIL_DEFAULT}，0 = 不带）",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="另存一份到指定文件（父目录不存在就建）",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """跑一遍。返回退出码（0 / 2）。"""
    from ba_mod_maker.app.diag import report

    count = max(0, int(args.tail if args.tail is not None else TAIL_DEFAULT))
    emitter = Emitter(json_mode=bool(args.json_out))

    health = report.health_line()
    env = report.env_lines()
    tail = report.recent_lines(count)

    wrote = ""
    if args.out:
        target = Path(args.out).expanduser()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(report.diagnostics_text(count=count), encoding="utf-8")
        except OSError as exc:
            emitter.failure(f"写不进 {target}", hint=f"{type(exc).__name__}: {exc}")
            return EXIT_UNWRITABLE
        wrote = str(target)

    _report_human(emitter, health=health, env=env, tail=tail, count=count, wrote=wrote)
    emitter.document(
        {
            "health": health,
            "version": report.version(),
            "python": platform.python_version(),
            "log_path": str(report.log_path()),
            "env": env,
            "tail": tail,
            "wrote": wrote,
        }
    )
    return EXIT_OK


def _report_human(
    emitter: Emitter,
    *,
    health: str,
    env: list[str],
    tail: list[str],
    count: int,
    wrote: str,
) -> None:
    """人读那一段（`--json` 时它自动改走 stderr，见 `Emitter`）。"""
    emitter.field("体检", health)
    emitter.blank()
    emitter.line("环境：")
    for line in env:
        emitter.line(f"  {line}")
    if count:
        emitter.blank()
        emitter.line(f"最近事件（日志尾部 {count} 行）：")
        for line in tail:
            emitter.line(f"  {line}")
    emitter.blank()
    if wrote:
        emitter.field("已落盘", wrote)
    else:
        emitter.line("想留一份给助手：--out <路径>"
                     "（或界面 ☰ -> 👁 视图 -> 🐞 调试… -> 💾 保存到文件）")


__all__ = ["EXIT_OK", "EXIT_UNWRITABLE", "TAIL_DEFAULT", "add_parser", "run"]
