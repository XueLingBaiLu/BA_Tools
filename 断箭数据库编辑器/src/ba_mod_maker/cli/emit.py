"""CLI 的统一输出层：人读文本与 `--json` **只有一个出口**。

## 为什么要有它

`docs/审查-代码规范化与长期可维护性.md` 的 P2-12：`cli/__main__.py` 里攒了 47 处裸
`print`，"哪些字是给机器看的"没有一处说了算 —— 于是 `--json` 只能靠在每个 `print`
旁边再补一个 `if`。新子命令（`validate` / `find`）一律经这里，不再加第 48 处。

## 四条约定

1. **stdout 只出产物**：不加 `--json` 时是人读文本，加了就是一个 JSON 文档 ——
   二选一，**绝不混**；
2. `--json` 时人读那几行**改走 stderr**（不是丢掉）：stdout 干净得能被 `json.loads`，
   而人在终端前照样看得见这份报告；失败时 stdout 也有一份 `ok: false` 的 JSON，
   所以 stdout 上永远不会出现非 JSON（退出码只说类别，说不清是哪个文件打不开）；
3. 对齐、缩进、结尾换行都归这里：调用方给 `("表 / 行", "24 张")`，不自己算宽度 ——
   `str.ljust` 数的是**字符个数**，中文一多就参差不齐（见 `display_width`）；
4. **标准流都掰成 UTF-8**（只动进程自己的那两个）：管道里（非控制台）Windows 会用 ANSI
   代码页，而数据里真有西里尔字母（`Weapons.HUDName` 的「2А72」，那个 А 是 U+0410）——
   cp936 编不出来，`--json` 会当场 UnicodeEncodeError。
"""

from __future__ import annotations

import json
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from contextlib import suppress
from typing import Any, TextIO

#: JSON 文档的版本号。消费方按它判断字段形状；**加字段不涨版本**。
SCHEMA_VERSION = 1

#: 人读输出里标签列的宽度（按**显示列**算，不是按字符个数）。
LABEL_WIDTH = 12


def display_width(text: str) -> int:
    """这段文字在终端里占几列。东亚宽字符（汉字）算 2 列，其余算 1。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text: str, width: int) -> str:
    """按显示宽度右侧补空格（`str.ljust` 数的是字符个数，中文会歪）。"""
    return text + " " * max(0, width - display_width(text))


def _force_utf8(stream: object) -> None:
    """把标准流掰成 UTF-8；不是标准流（用例注入的 StringIO）就不动。"""
    if stream is not sys.stdout and stream is not sys.stderr:
        return
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:  # pragma: no cover - 只在换了标准流的宿主里
        return
    # 流已关 / 已被别人包装时它抛 ValueError/OSError：那不是这里的错，忽略。
    with suppress(ValueError, OSError):
        reconfigure(encoding="utf-8")


def force_utf8_streams() -> None:
    """在 **argparse 之前**掰标准流。

    `--help` 的输出**不走 Emitter**（argparse 直接 print），所以没有这一步时，
    帮助文本里带 ⛔/✅ 的子命令（实测 `skin` / `assets`）在 GBK 管道下当场
    `UnicodeEncodeError` ⇒ 用户只是敲个 `--help` 就看到 traceback。
    与 Emitter 用同一份实现（`_force_utf8`），不改编码策略。
    """
    _force_utf8(sys.stdout)
    _force_utf8(sys.stderr)


class Emitter:
    """一个命令一个：人读文本走 `line` / `field`，机器可读走 `document`。

    ```python
    em = Emitter(json_mode=args.json_out)
    em.field("源", loaded.path)          # --json 时这些行去 stderr
    em.document({"source": str(loaded.path)})   # 只在 --json 时真的写 stdout
    ```
    """

    def __init__(
        self,
        *,
        json_mode: bool = False,
        out: TextIO | None = None,
        err: TextIO | None = None,
        label_width: int = LABEL_WIDTH,
    ) -> None:
        self.json_mode = json_mode
        self.label_width = label_width
        self._out = out if out is not None else sys.stdout
        self._err = err if err is not None else sys.stderr
        #: 人读文本的去处：不加 --json 时是 stdout，加了就退到 stderr（见模块头第 2 条）。
        self._human = self._err if json_mode else self._out
        if out is None:
            _force_utf8(self._out)
        if err is None:
            _force_utf8(self._err)

    # ------------------------------------------------------------------ #
    # 人读
    # ------------------------------------------------------------------ #

    def line(self, text: str = "") -> None:
        """人读模式的一行。"""
        print(text, file=self._human)

    def field(self, label: str, value: object) -> None:
        """人读模式的「标签 : 值」一行。"""
        self.line(f"{pad(label, self.label_width)}: {value}")

    def blank(self) -> None:
        self.line()

    def items(self, texts: Sequence[str], *, indent: int = 2, bullet: str = "·") -> None:
        """一组带项目符号的行（命中的例子、引用者清单那种）。"""
        prefix = " " * indent
        for text in texts:
            self.line(f"{prefix}{bullet} {text}" if bullet else f"{prefix}{text}")

    # ------------------------------------------------------------------ #
    # 机器可读
    # ------------------------------------------------------------------ #

    def document(self, payload: Mapping[str, Any]) -> None:
        """整个命令的产物：一个 JSON 文档 ``{"schema": 1, "ok": true, ...}``。

        ``schema`` / ``ok`` 由这里统一加，命令侧只管自己的字段；
        命令侧真给了同名键以命令侧为准（故意留的口子，正常用不到）。
        """
        if not self.json_mode:
            return
        merged: dict[str, Any] = {"schema": SCHEMA_VERSION, "ok": True, **payload}
        print(json.dumps(merged, ensure_ascii=False, indent=2), file=self._out)

    # ------------------------------------------------------------------ #
    # 出错
    # ------------------------------------------------------------------ #

    def error(self, message: str, *, hint: str | None = None) -> None:
        """一句人话的错误 —— **永远走 stderr**（它在 stdout 之外，`--json` 也不碍事）。"""
        print(f"错误：{message}" + (f"（建议：{hint}）" if hint else ""), file=self._err)

    def failure(self, message: str, *, hint: str | None = None) -> None:
        """整个命令失败：stderr 一句人话 + `--json` 时 stdout 一份 `ok:false` 文档。"""
        self.error(message, hint=hint)
        self.document({"ok": False, "error": {"message": message, "hint": hint}})


__all__ = [
    "LABEL_WIDTH",
    "SCHEMA_VERSION",
    "Emitter",
    "display_width",
    "force_utf8_streams",
    "pad",
]
