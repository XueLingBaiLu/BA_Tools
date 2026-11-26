"""`object` 子命令：**修改 / 删除**包里的一个对象（零 Qt，走资产账本 ＋ Δ 计划）。

## 这一步是什么（设计稿 `docs/设计-资产编辑入口.md` §5 / §7 第 3 步）

产品的资产侧只有**三个原子动作**：修改 / 新增 / 删除（ADR 0008 决定 1）。
CLI 这一刀三个都落（`add` 是 2026-10-12 补上的第三个）：

    ba-mod-maker object set    --bundle <包> --path-id <N> --field <typetree 路径> --value <新值>
    ba-mod-maker object add    --bundle <包> --from <规格文件.json>
    ba-mod-maker object delete --bundle <包> --path-id <N>

形状照现成件：`add_parser(sub)` / `run(args)` 照 `cli/finder.py`；人读与 `--json` 只有一个出口
（`cli/emit.py::Emitter`）；退出码沿用 `__main__.EXIT_CODES_HELP`
（0 完成 / 1 结果不干净 / 2 用不了）。

⛔ **仍然没有第四个动词**：地址线两条（`address add` / `address delete`）形状未定，
按设计稿排在后面。也**不许**出现「以 B 顶替 A」那种复合入口
（`object swap` / `--replace` / `--to`）—— 两把尺子盯着这件事：

* **J3 文案**（`tests/test_no_swap_wording.py`，既有）：面 ① 是**从 `_build_parser()` 起递归
  `format_help()`** 取到的 CLI 全部子命令帮助文本 ⇒ 本模块一注册 `object`，它的 help
  **自动进入量程**（`tests/test_cli_object_edit.py` 的
  `test_object_help_text_is_clean_and_visible_to_the_j3_ruler` 与
  `test_object_is_wired_into_the_product_cli_parser` 守着这条，而且能红）；
* **J4 结构**（`tests/test_cli_object_edit.py`）：一次动作只许发一个动词。

## 三种跑法：算 / 写计划 / 真落字节

| 跑法 | 盘上会发生什么 |
|---|---|
| 默认 | 只落一份 **Δ 计划** `<工程目录>/patches/asset-plan.json`（一个字节都不碰包） |
| `--dry-run` | **一个字节都不写**（连计划都不落）——「先算出来看一眼」用它 |
| `--write` | **真落字节**：`write_delta` / `write_removals` → 补丁柜 → 成对更新 catalog CRC |

一次调用**只发一个动词**：`build_plan()` 只调 `set_field` / `add_object` / `delete_object` 之一，
所以交出来的 `AssetPlan` 必然只有一格非空。

`--write` 那一趟里：等长的 `set` 走 `ObjectChange`（原地覆盖），**变长**的走 `ObjectGrowth`
（搬到 node[0] 尾部、8 字节对齐），`add` 走 `NewObject`（对象表插条目 + payload 追加尾部），
`delete` 走 `ObjectRemoval`。⛔ 三条红线（游戏在跑拦下 / 补丁柜跟随游戏盘 / 写后复核）
全在 `writer._write_bundle` 里，本模块**不另写一套**。

## 新增一个对象时，载荷与类型从哪来（`--from` 那份规格文件）

`object add` 的载荷是**已经序列化好的对象字节**（本命令不负责造它 —— 造它的是
`asset_kernel/clone.py` 那条链或别的工具）。规格文件是一份 JSON：

    {"schema_version": 1, "kind": "asset-add",
     "path_id": 9001,              ← 新对象的 pathID（⛔ 不许与包内任何已有 pathID 撞）
     "class_id": 28,               ← 与 "type_id" **二选一**：给 classID，本命令去目标包的类型表里认
     "payload": "payload.bin",     ← 相对规格文件所在目录
     "payload_sha256": "…",        ← 可选；给了就核（⛔ 不核等于没给）
     "name": "…", "type_name": "Texture2D"}   ← 可选；只进读数

`class_id` 那一格解决的是「不同包里类型表下标不一样」这件事：`NewObject.type_id` 要的是
**类型表下标**，它随包变；`classID` 才是类型的名字。⛔ 一个包里同一个 classID 有多棵类型树
（MonoBehaviour 就是）时**当场拒绝并列候选**，不猜。

## 载荷自证（写之前先核一遍）

新对象的字节必须**能被它自己那棵类型树解出来、且再序列化回逐字节相同的原样**
（`clone.read_object` → `clone.write_object`）。这一条 fail-closed：解不动就不给写 ——
否则「对象表里多了一条读不出来的东西」要到真机上才炸。

## `--field` 是 typetree 路径（三种写法，纯函数解析）

    a.b                 按**名字**一层层往里走
    a[0]                按**下标**取（序列；负数按 Python 口径）
    a[Layer_A97CDC25]   按**名字**取（Unity 的 pair 表：先按名字找到那一对，再 `.second` 取它的值）

    m_SavedProperties.m_TexEnvs[Layer_A97CDC25].second.m_PathID

（设计稿 §5 把前面那一截省着写成 `m_TexEnvs[...]`；真实材质 typetree 里它在
`m_SavedProperties` 底下 —— `skin_install._tex_envs` 读的就是这一层，别照缩写抄。）

`.first` / `.second` 是 Unity typetree 里 **pair 节点**的两个半边；读到 2 元序列时按这个口径
取 `[0]` / `[1]`（`skin_install` 那条链读的就是 `entry[0]` / `entry[1]`，同一份数据两个写法）。
⛔ **读不出来就报错并把可用的键 / 名字印出来**（`resolve_field`）—— 静默返回 `None` 会让
「写了一个根本不存在的字段」看起来像成功。

## 两个读侧接缝（用例的注入点）

* `read_object_bytes` —— 走 `core/asset/ufs.py`（**产品自己的容器读侧，不用 UnityPy**）；
* `read_typetree` —— 走 `asset_kernel/unitypy_source.py`（产品**唯一**的 typetree 入口：
  「te 工单 §9 点名的四个读侧入口必须只有一份实现」）。

用例把**合成** typetree 从 `read_typetree` 喂进来，于是纯函数那半条链不需要真包 ——
合成容器的 SerializedFile 没有类型树（`tests/test_writeback_l1.py` 文件头逐字写着这条），
UnityPy 解不出它的 typetree。**这是那批夹具的已知边界，不是这里绕开了什么**。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import struct
from collections.abc import Callable, Mapping, MutableMapping, MutableSequence, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from ba_mod_maker.cli.emit import Emitter
from ba_mod_maker.core.edit.asset_changeset import MISSING, AssetChangeset, AssetEdit, AssetPlan
from ba_mod_maker.core.edit.asset_plan_io import plan_document, write_plan
from ba_mod_maker.core.edit.asset_refs import AssetRef, FieldPath
from ba_mod_maker.core.errors import AssetError, BaModMakerError

__all__ = [
    "EXIT_DONE",
    "EXIT_NOTHING",
    "EXIT_UNUSABLE",
    "SPEC_KIND",
    "SPEC_SCHEMA_VERSION",
    "VERBS",
    "AddSpec",
    "Step",
    "add_parser",
    "assign_field",
    "build_plan",
    "coerce_field_value",
    "parse_field_path",
    "read_add_spec",
    "read_object_bytes",
    "read_object_nodes",
    "read_type_class_ids",
    "read_typetree",
    "render_steps",
    "resolve_field",
    "resolve_type_id",
    "run",
    "write_edit",
]

#: 退出码。与 `finder.py` / `validator.py` 同一套语义（0 完成 / 1 空结果 / 2 用不了）。
EXIT_DONE = 0
EXIT_NOTHING = 1
EXIT_UNUSABLE = 2

#: 这一刀落的三个原子动词（顺序 = `--help` 里的顺序）。
VERBS: Final[tuple[str, str, str]] = ("set", "add", "delete")

#: `object add --from` 那份规格文件的 kind 与版本（形状变了就 +1）。
SPEC_KIND: Final[str] = "asset-add"
SPEC_SCHEMA_VERSION: Final[int] = 1

#: 名字那一跳读到哪停（点与方括号是分隔符）。
_NAME_STOP = ".[]"
#: 方括号里可以用的两种引号。
_QUOTES = ("'", '"')
#: Unity typetree 里 **pair 节点**的两个半边。
_PAIR_SIDES: Final[tuple[str, str]] = ("first", "second")
#: 报错话术里最多印几个可用键（一屏放不下 21 个贴图槽）。
_PREVIEW_LIMIT = 12
_INTEGER = re.compile(r"-?\d+")

_DESCRIPTION = """修改 / 新增 / 删除包里某一个对象（三个原子动作；没有"以 B 顶替 A"那种复合动作）。

三种写法：
  object set    --bundle <包> --path-id <N> --field <typetree 路径> --value <新值>
  object add    --bundle <包> --from <规格文件.json>
  object delete --bundle <包> --path-id <N>

--field 是 **typetree 路径**，三种写法都能用：
  a.b                 按名字一层层往里走
  a[0]                按下标取（序列）
  a[Layer_A97CDC25]   按名字取（Unity 的 pair 表：先按名字找到那一对，再 .second 取值）
例：m_SavedProperties.m_TexEnvs[Layer_A97CDC25].second.m_PathID

跑法三档：默认**只写 Δ 计划**（<工程目录>/patches/asset-plan.json）· `--dry-run` 一个字节都不写 ·
`--write` **真落字节**（Δ ＋ 反向 Δ 补丁柜 ＋ 成对更新 catalog CRC）。默认那档不碰游戏目录、不写包。

退出码：
  0  算出来了 / 计划已写出 / --write 已写回
  1  没有改动可写（新值与旧值相同）
  2  用不了（包打不开 / 没有这个 pathID / 字段路径对不上 / --value 转不了 / 规格或载荷不对）
"""

_EPILOG = """--value 的类型照**旧值**那一格定（布尔 / 整数 / 小数 / 字符串）；null 时纯数字优先。
--dry-run       只算不写（工程目录与包都零写入）。
--write         真写：Δ ＋ 反向 Δ 补丁柜 ＋ 成对更新 catalog CRC（writer 那条链）。
--project-root  Δ 计划与补丁柜的落点（默认当前目录）。
--game/--catalog  --write 用：游戏根与 catalog.json（不给就自动找；推出来的不存在就不同步）。

object add 的规格文件（JSON）：
  {"schema_version": 1, "kind": "asset-add", "path_id": 9001, "class_id": 28,
   "payload": "payload.bin", "payload_sha256": "…", "name": "…", "type_name": "Texture2D"}
  class_id 与 type_id **二选一**：给 class_id 更省事（本命令去目标包的类型表里认下标）。

例：
  ba-mod-maker object set --bundle units.bundle --path-id 58076 --field m_PathID --value 58077
  ba-mod-maker object add --bundle units.bundle --from 新增.json --write --game <游戏根>
  ba-mod-maker object delete --bundle units.bundle --path-id 9001 --project-root D:/proj
"""


# --------------------------------------------------------------------------- #
# 纯函数：typetree 路径 -> 一跳一跳
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Step:
    """typetree 路径上的**一跳**。

    * `attr`  —— 点号后面那个名字（`a.b`）；
    * `index` —— 方括号里是整数（`a[0]`）；
    * `key`   —— 方括号里是名字（`a[Layer_A97CDC25]`）。
    """

    kind: Literal["attr", "index", "key"]
    value: str | int


def parse_field_path(text: str) -> tuple[Step, ...]:
    """**纯函数**：把 `a.b[0][key]` 解析成一串 [Step]。

    语法只有两条拼法（没有隐式规则）：

        a.b          名字与名字之间用 `.`
        a[0] / a[k]  下标或名字写在方括号里；方括号后面还能跟 `.` 或另一个方括号

    :raises AssetError: 空路径 / 连续的 `.` / 方括号不闭合或为空 / 方括号内容带方括号
        —— 消息里带着**第几个字符**出问题，不做"猜一个能用的"那种修正。
    """
    if not isinstance(text, str):
        raise AssetError(f"字段路径必须是字符串，拿到 {type(text).__name__}")
    source = text.strip()
    if not source:
        raise AssetError(
            "字段路径是空的",
            hint="例：m_SavedProperties.m_TexEnvs[Layer_A97CDC25].second.m_PathID",
        )

    steps: list[Step] = []
    index = 0
    want_name = True
    while index < len(source):
        char = source[index]
        if char == ".":
            if want_name:
                raise AssetError(
                    f"字段路径 {text!r} 里第 {index + 1} 个字符是 '.'，它前面没有名字",
                    hint="两种接法：a.b（名字之间用点）与 a[i]（下标 / 名字用方括号）",
                )
            index += 1
            want_name = True
            continue
        if char == "[":
            closing = _closing_bracket(source, index, text=text)
            steps.append(_bracket_step(source[index + 1 : closing], text=text, at=index + 1))
            index = closing + 1
            want_name = False
            continue
        if not want_name:
            raise AssetError(
                f"字段路径 {text!r} 里第 {index + 1} 个字符是 {char!r}，"
                "它前面那一跳之后只能跟 '.' 或 '['",
                hint="a[0]b 这种写法不成立；a[0].b 才是完整的一跳接一跳",
            )
        stop = index
        while stop < len(source) and source[stop] not in _NAME_STOP:
            stop += 1
        name = source[index:stop]
        if stop < len(source) and source[stop] == "]":
            raise AssetError(
                f"字段路径 {text!r} 里的 ']' 没有对应的 '['（第 {stop + 1} 个字符）",
                hint="名字里不要带方括号；要取带方括号的键就写进方括号那一档",
            )
        if not name:  # pragma: no cover -- 上面两条已经把能到这个分支的输入挡掉了
            raise AssetError(f"字段路径 {text!r} 里第 {index + 1} 个字符起读不出名字")
        steps.append(Step(kind="attr", value=name))
        index = stop
        want_name = False

    if want_name:
        raise AssetError(f"字段路径 {text!r} 以 '.' 结尾，后面没有名字")
    return tuple(steps)


def _closing_bracket(source: str, start: int, *, text: str) -> int:
    """找第 `start` 个 `[` 的配对 `]`（引号里的方括号不算）。"""
    quote = ""
    index = start + 1
    while index < len(source):
        char = source[index]
        if quote:
            if char == quote:
                quote = ""
        elif char in _QUOTES:
            quote = char
        elif char == "]":
            return index
        index += 1
    raise AssetError(
        f"字段路径 {text!r} 里第 {start + 1} 个字符处的 '[' 没有对应的 ']'",
        hint="方括号要成对",
    )


def _bracket_step(inner: str, *, text: str, at: int) -> Step:
    """方括号里那一跳：引号包起来的是**名字**，纯数字是**下标**，其余也按名字。"""
    if not inner.strip():
        raise AssetError(
            f"字段路径 {text!r} 里第 {at} 个字符处的方括号是空的",
            hint="写一个下标（a[0]）或一个名字（a[Layer_A97CDC25]）",
        )
    body = inner.strip()
    if len(body) >= 2 and body[0] in _QUOTES and body[-1] == body[0]:
        key = body[1:-1]
        if not key:
            raise AssetError(f"字段路径 {text!r} 里第 {at} 个字符处是一对空引号")
        if "[" in key or "]" in key:
            raise AssetError(
                f"字段路径 {text!r} 里第 {at} 个字符处的键里面还有方括号：{key!r}",
                hint="带方括号的键解不出来 —— 换个落点，别在这里猜",
            )
        return Step(kind="key", value=key)
    if "[" in body or "]" in body:
        raise AssetError(
            f"字段路径 {text!r} 里第 {at} 个字符处的方括号里还有方括号",
            hint="键里要带方括号就用引号包起来（a['x[1]']）",
        )
    if body[0] in _QUOTES or body[-1] in _QUOTES:
        raise AssetError(
            f"字段路径 {text!r} 里第 {at} 个字符处的引号没有配对",
            hint='两侧都要写：a["Layer_A"]',
        )
    if _INTEGER.fullmatch(body):
        return Step(kind="index", value=int(body, 10))
    return Step(kind="key", value=body)


def render_steps(steps: Sequence[Step]) -> str:
    """把跳序列画回一行路径（报错话术里说"走到哪一跳"用的）。"""
    out = ""
    for step in steps:
        if step.kind == "attr":
            out += ("." if out else "") + str(step.value)
        else:
            out += f"[{step.value}]"
    return out or "（根）"


def resolve_field(tree: Any, steps: Sequence[Step]) -> Any:
    """**纯函数**：按 `steps` 走到底，返回那一格**现在**的值。

    :raises AssetError: 走不到 —— 消息里带**走到哪一跳**与**那一层的可用键 / 名字**。
    """
    current = tree
    for position, step in enumerate(steps):
        current = _step_into(current, step, trail=render_steps(steps[: position + 1]))
    return current


def _step_into(current: Any, step: Step, *, trail: str) -> Any:
    if step.kind == "attr":
        return _attr_into(current, str(step.value), trail=trail)
    if step.kind == "index":
        return _index_into(current, int(step.value), trail=trail)
    return _key_into(current, str(step.value), trail=trail)


def _attr_into(current: Any, name: str, *, trail: str) -> Any:
    if isinstance(current, Mapping):
        if name in current:
            return current[name]
        raise AssetError(
            f"路径 {trail} 对不上：这一层没有 {name!r}",
            hint=f"可用的键：{_mapping_preview(current)}",
        )
    if _is_sequence(current):
        if len(current) == 2 and name in _PAIR_SIDES:
            # Unity 的 pair 节点：typetree 里写出来就是 first / second 两个半边。
            return current[_PAIR_SIDES.index(name)]
        raise AssetError(
            f"路径 {trail} 对不上：这一层是一个 {len(current)} 项的序列，取不到名字 {name!r}",
            hint="序列要么用下标（a[0]），要么用名字取 pair 表里的那一项（a[那名字]）",
        )
    raise AssetError(
        f"路径 {trail} 对不上：{_kind_of(current)} 上取不到名字 {name!r}",
        hint="能往下走的只有映射（按键取）与序列（按下标 / pair 名字取）",
    )


def _index_into(current: Any, index: int, *, trail: str) -> Any:
    if _is_sequence(current):
        if -len(current) <= index < len(current):
            return current[index]
        raise AssetError(
            f"路径 {trail} 越界：这一层有 {len(current)} 项，取不到下标 {index}",
            hint=f"可用下标：0 .. {len(current) - 1}" if current else "这一层是空的",
        )
    if isinstance(current, Mapping):
        for candidate in (index, str(index)):
            if candidate in current:
                return current[candidate]
        raise AssetError(
            f"路径 {trail} 对不上：这一层没有下标 / 键 {index}",
            hint=f"可用的键：{_mapping_preview(current)}",
        )
    raise AssetError(f"路径 {trail} 对不上：{_kind_of(current)} 不能用 [{index}] 取")


def _key_into(current: Any, key: str, *, trail: str) -> Any:
    if isinstance(current, Mapping):
        if key in current:
            return current[key]
        raise AssetError(
            f"路径 {trail} 对不上：这一层没有 {key!r}",
            hint=f"可用的键：{_mapping_preview(current)}",
        )
    if _is_sequence(current):
        names: list[str] = []
        for entry in current:
            head = _pair_head(entry)
            if head is None:
                continue
            names.append(head)
            if head == key:
                return entry
        hint = (
            f"可用的名字：{_list_preview(names)}"
            if names
            else "这一层不是「名字 -> 值」的表（它的每一项都不是 pair）"
        )
        raise AssetError(
            f"路径 {trail} 对不上：这一层的序列里没有名字叫 {key!r} 的那一项",
            hint=hint,
        )
    raise AssetError(f"路径 {trail} 对不上：{_kind_of(current)} 不能用 [{key!r}] 取")


def _is_sequence(value: Any) -> bool:
    """是不是"能按下标 / pair 名字取"的序列（字符串与字节不算 —— 它们是标量）。"""
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _pair_head(entry: Any) -> str | None:
    """一对「名字 -> 值」的**名字**那一半（认 Unity 的 first / second，也认 2 元序列）。"""
    if isinstance(entry, Mapping):
        return str(entry["first"]) if "first" in entry else None
    if _is_sequence(entry) and len(entry) == 2:
        return str(entry[0])
    return None


def _kind_of(value: Any) -> str:
    return "null" if value is None else type(value).__name__


def _mapping_preview(mapping: Mapping[Any, Any]) -> str:
    return _list_preview([str(key) for key in mapping])


def _list_preview(names: Sequence[str]) -> str:
    if not names:
        return "（这一层是空的）"
    head = "、".join(repr(name) for name in names[:_PREVIEW_LIMIT])
    if len(names) <= _PREVIEW_LIMIT:
        return head
    return f"{head} …（共 {len(names)} 个）"


# --------------------------------------------------------------------------- #
# 纯函数：把某一跳写成一个新值（读侧的镜像；原树不动）
# --------------------------------------------------------------------------- #
def assign_field(tree: Any, steps: Sequence[Step], value: Any) -> Any:
    """**纯函数**：把 `steps` 指到的那一格改成 `value`，返回**新的**树（原树一个字节不动）。

    与 `resolve_field` 共用同一套走法（`_step_into`）与同一套话术 —— **读得到的地方才写得进去**：
    字段不存在 / 下标越界 / 名字对不上，写侧同样是 `AssetError`，⛔ 不静默加一格。

    先 `deepcopy` 再走：对象级改动一次就是几百字节到几 MB，深拷贝比"手写一条复制链"便宜得多，
    也不会漏掉嵌套的共享子结构。
    """
    if not steps:
        raise AssetError("字段路径是空的：没有可写的那一跳")
    root = copy.deepcopy(tree)
    current = root
    for position, step in enumerate(steps[:-1]):
        current = _step_into(current, step, trail=render_steps(steps[: position + 1]))
    _put_into(current, steps[-1], value, trail=render_steps(steps))
    return root


def _put_into(current: Any, step: Step, value: Any, *, trail: str) -> None:
    """最后一跳：按 `step` 的三种写法把 `value` 放进 `current`（改的是那棵**副本**）。"""
    if step.kind == "attr":
        _put_attr(current, str(step.value), value, trail=trail)
    elif step.kind == "index":
        _put_index(current, int(step.value), value, trail=trail)
    else:
        _put_key(current, str(step.value), value, trail=trail)


def _put_attr(current: Any, name: str, value: Any, *, trail: str) -> None:
    if isinstance(current, MutableMapping):
        if name not in current:
            raise AssetError(
                f"路径 {trail} 对不上：这一层没有 {name!r}",
                hint=f"可用的键：{_mapping_preview(current)}",
            )
        current[name] = value
        return
    if _is_sequence(current) and len(current) == 2 and name in _PAIR_SIDES:
        if not isinstance(current, MutableSequence):
            raise AssetError(
                f"路径 {trail} 落在不可变的 pair 上（{_kind_of(current)}）—— 那一格改不了",
                hint="pair 在 typetree 里正常是 list（[名字, 值]）或 dict；元组形状只读",
            )
        current[_PAIR_SIDES.index(name)] = value
        return
    raise AssetError(
        f"路径 {trail} 对不上：{_kind_of(current)} 上写不进名字 {name!r}",
        hint="能写的只有映射（按键写）与两半的 pair（first / second）",
    )


def _put_index(current: Any, index: int, value: Any, *, trail: str) -> None:
    if _is_sequence(current):
        if not isinstance(current, MutableSequence):
            raise AssetError(f"路径 {trail} 落在不可变序列上（{_kind_of(current)}）—— 那一格改不了")
        if -len(current) <= index < len(current):
            current[index] = value
            return
        raise AssetError(
            f"路径 {trail} 越界：这一层有 {len(current)} 项，写不进下标 {index}",
            hint=f"可用下标：0 .. {len(current) - 1}" if current else "这一层是空的",
        )
    if isinstance(current, MutableMapping):
        for candidate in (index, str(index)):
            if candidate in current:
                current[candidate] = value
                return
        raise AssetError(
            f"路径 {trail} 对不上：这一层没有下标 / 键 {index}",
            hint=f"可用的键：{_mapping_preview(current)}",
        )
    raise AssetError(f"路径 {trail} 对不上：{_kind_of(current)} 不能用 [{index}] 写")


def _put_key(current: Any, key: str, value: Any, *, trail: str) -> None:
    if isinstance(current, MutableMapping):
        if key not in current:
            raise AssetError(
                f"路径 {trail} 对不上：这一层没有 {key!r}",
                hint=f"可用的键：{_mapping_preview(current)}",
            )
        current[key] = value
        return
    if _is_sequence(current) and isinstance(current, MutableSequence):
        names: list[str] = []
        for position, entry in enumerate(current):
            head = _pair_head(entry)
            if head is None:
                continue
            names.append(head)
            if head == key:
                current[position] = _pair_with(entry, value)
                return
        raise AssetError(
            f"路径 {trail} 对不上：这一层的序列里没有名字叫 {key!r} 的那一项",
            hint=(f"可用的名字：{_list_preview(names)}" if names
                  else "这一层不是「名字 -> 值」的表（它的每一项都不是 pair）"),
        )
    raise AssetError(f"路径 {trail} 对不上：{_kind_of(current)} 不能用 [{key!r}] 写")


def _pair_with(entry: Any, value: Any) -> Any:
    """把「名字 -> 值」那一对里的**值**换成 `value`，形状照旧（dict 还是 dict、list 还是 list）。"""
    if isinstance(entry, Mapping):
        out = dict(entry)
        out["second"] = value
        return out
    return [entry[0], value]


# --------------------------------------------------------------------------- #
# 纯函数：--value -> 与旧值同类型的值
# --------------------------------------------------------------------------- #
def coerce_field_value(old: Any, text: str) -> Any:
    """**纯函数**：把命令行给的字符串转成**与旧值同一格类型**的值。

    口径照 `cli/__main__.py::_coerce`（那条命令改的是数据库单元格，这条改的是资产字段 ——
    「一个格子的类型由它现在的值决定」这件事只有一份口径）：

    * 旧值是布尔 -> 认 true/false 与 1/0（也认 yes/no/on/off）；
    * 旧值是整数 -> 按十进制读；旧值是小数字 -> `float()`；
    * 旧值是字符串 -> 原样收；
    * 旧值是 `null` -> 先试整数、再试小数、最后当字符串。

    :raises AssetError: 转不了（例：往整数那一格写 `Layer_A`），或者这一格不是标量。
    """
    if isinstance(old, bool):
        lowered = text.strip().casefold()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise AssetError(f"这一格是布尔值，收到 {text!r}", hint="写 true / false（也认 1 / 0）")
    if isinstance(old, int):
        try:
            return int(text.strip(), 10)
        except ValueError:
            raise AssetError(
                f"这一格是整数，转不了 {text!r}",
                hint="写一个十进制整数，例 58077",
            ) from None
    if isinstance(old, float):
        try:
            value = float(text.strip())
        except ValueError:
            raise AssetError(f"这一格是小数，转不了 {text!r}", hint="例 0.75") from None
        # ⚠ 这一格在文件里是 **float32**：先按 f32 收一次，让「计划里的新值」
        #    与「盘上读回的值」**逐位相同**。
        #    不这么做，写 0.2 在盘上会变成 0.20000000298023224，而写后复核是逐字段比对的
        #    ⇒ 判成「差 1 处」的**假红**（实测：effects_entry 的写后读回）。
        #    0.75 / 13.5 这类 f32 精确值看不出这个问题。
        return struct.unpack("<f", struct.pack("<f", value))[0]
    if isinstance(old, str):
        return text
    if old is None:
        stripped = text.strip()
        for cast in (int, float):
            try:
                return cast(stripped)
            except ValueError:
                continue
        return text
    raise AssetError(
        f"这一格的值是 {_kind_of(old)}，--value 只认标量（布尔 / 整数 / 小数 / 字符串）",
        hint="对象 / 列表 / 字节级的落点不在这一条命令的能力范围内",
    )


# --------------------------------------------------------------------------- #
# 纯函数：一次动作 -> 一份只含**一个动词**的计划
# --------------------------------------------------------------------------- #
def build_plan(
    *,
    verb: str,
    ref: AssetRef,
    baseline: Callable[[AssetRef], Any],
    value: str | None = None,
    payload: bytes | None = None,
) -> AssetPlan:
    """**纯函数**（不碰盘、不读文件）：发一个动词，交出一份 [AssetPlan]。

    * `set`    -> `AssetChangeset.set_field`（`ref.field` 必须是 [FieldPath]）；
    * `add`    -> `AssetChangeset.add_object`（`ref.field` 必须是 `None`，要一份 `payload`）；
    * `delete` -> `AssetChangeset.delete_object`（同上，不要载荷）。

    一次调用**只调一个方法** —— 于是「一次动作同时产生删除与新增」在结构上就写不出来
    （ADR 0008 决定 2 的第一道锁；第二道锁在 `add_object` 的同 pathID 守卫里）。
    """
    if verb not in VERBS:
        raise AssetError(f"不认识的动作 {verb!r}", hint=f"这一刀只有 {' / '.join(VERBS)} 三个")
    changeset = AssetChangeset(baseline)
    if verb == "set":
        if not isinstance(ref.field, FieldPath):
            raise AssetError(f"{ref} 没有字段落点：set 改的就是一个字段")
        old = baseline(ref)
        if value is None:
            raise AssetError(
                "set 要 --value（新值）",
                hint="--dry-run 也一样要：判据里有旧值 -> 新值",
            )
        changeset.set_field(ref, coerce_field_value(old, value))
    elif verb == "add":
        if payload is None:
            raise AssetError(
                "add 要一份载荷（新对象的字节）",
                hint="载荷在 --from 那份规格文件里（见模块头那段形状）",
            )
        changeset.add_object(ref, payload)
    else:
        changeset.delete_object(ref)
    return changeset.plan()


# --------------------------------------------------------------------------- #
# 读侧两个接缝（用例从这里喂合成数据）
# --------------------------------------------------------------------------- #
def read_object_bytes(bundle: str | Path, path_id: int) -> bytes | None:
    """读包里某个对象**现在的字节**；没有这个 pathID 时返回 `None`。

    走 `core/asset/ufs.py`（产品自己的容器读侧），所以**不需要 UnityPy**，也不需要那个包有
    类型树 —— 合成小容器照样能读（`tests/test_cli_object_edit.py` 走的就是它）。非 SerializedFile
    的节点（`.resS` 那类）解析会抛异常，跳过即可。
    """
    from ba_mod_maker.core.asset import ufs

    with ufs.UfsReader(bundle) as reader:
        for node in reader.nodes:
            try:
                info = ufs.read_serialized_file(reader, node)
            except (BaModMakerError, struct.error):
                continue
            for _index, pid, stored, size, _type_id in info.objects:
                if pid == path_id:
                    start = node.offset + info.data_offset + stored
                    return reader.read_range(start, size)
    return None


def read_typetree(bundle: str | Path, path_id: int) -> Mapping[str, Any]:
    """读包里某个对象的 **typetree**（唯一入口：`asset_kernel/unitypy_source.py`）。

    这是**注入点**：用例把合成 typetree 从这里喂进来，于是 `--field` 那半条链不需要真包，
    也不需要 UnityPy 解得动那个包。

    :raises AssetError: 包里没有这个 pathID / 这个对象没有类型树 / 装不了 UnityPy。
    """
    from ba_mod_maker.asset_kernel import unitypy_source

    try:
        with unitypy_source.open_source(bundle) as source:
            return source.read_typetree(path_id)
    except KeyError as exc:
        raise AssetError(
            f"{Path(bundle).name} 里没有 pathID={path_id} 这个对象",
            hint="pathID 抄错了？先拿 catalog / 容器条目复核一遍再填",
        ) from exc
    except ImportError as exc:  # pragma: no cover -- pyproject 里 UnityPy 是运行时依赖
        raise AssetError(
            f"读不了 {Path(bundle).name} 的类型树：没装 UnityPy（{exc}）",
            hint="装运行时依赖：pip install UnityPy",
        ) from exc


# --------------------------------------------------------------------------- #
# 新增对象：规格文件 + 目标包的类型表（只读；⛔ 不猜）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class AddSpec:
    """`object add --from` 读出来的一份规格（**纯数据**：载荷已经在内存里）。

    形状见模块头「新增一个对象时，载荷与类型从哪来」。
    """

    path_id: int
    class_id: int | None
    type_id: int | None
    payload: bytes
    payload_source: Path
    name: str = ""
    type_name: str = ""

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


def read_add_spec(path: str | Path) -> AddSpec:
    """读一份新增对象的规格文件。⛔ 形状不对就抛，消息里带**哪一格**不对（不猜、不补默认值）。"""
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AssetError(
            f"读不出规格文件 {source}：{exc}",
            hint="它是一份 JSON：形状见 object add --help",
        ) from exc
    kind = document.get("kind") if isinstance(document, Mapping) else None
    if not isinstance(document, Mapping) or kind != SPEC_KIND:
        raise AssetError(
            f"{source} 不是一份新增对象的规格（kind={kind!r}）",
            hint=f"kind 要写 {SPEC_KIND!r}；Δ 计划（asset-plan.json）是另一个文件，别读混了",
        )
    version = document.get("schema_version")
    if version != SPEC_SCHEMA_VERSION:
        raise AssetError(
            f"规格文件的 schema_version={version!r}，本命令认 {SPEC_SCHEMA_VERSION}",
            hint="换一份对得上版本的规格文件；⛔ 不按旧形状猜",
        )
    path_id = _spec_int(document, "path_id", what="新对象的 pathID")
    if path_id is None:
        raise AssetError("规格文件缺 path_id（新对象的 pathID）")
    if path_id <= 0:
        raise AssetError(
            f"path_id={path_id}：新对象的 pathID 必须是正数",
            hint="用 asset_kernel/writer.py::next_path_id(那个包) 取一个（它按包内最大值 +1）",
        )
    type_id = _spec_int(document, "type_id", what="类型表下标")
    class_id = _spec_int(document, "class_id", what="classID")
    if (type_id is None) == (class_id is None):
        raise AssetError(
            "规格文件里 type_id 与 class_id 必须**正好给一个**"
            f"（现在 type_id={type_id!r} / class_id={class_id!r}）",
            hint="给 class_id 更省事：本命令去目标包的类型表里认下标；"
                 "同一 classID 有多棵树（MonoBehaviour）时才必须点名 type_id",
        )
    raw_payload = document.get("payload")
    if not isinstance(raw_payload, str) or not raw_payload.strip():
        raise AssetError("规格文件的 payload 那一格必须是一个文件路径（相对规格文件所在目录）")
    payload_path = (source.parent / raw_payload).resolve()
    try:
        payload = payload_path.read_bytes()
    except OSError as exc:
        raise AssetError(
            f"读不出载荷 {payload_path}：{exc}",
            hint="payload 那一格是**相对规格文件所在目录**的路径",
        ) from exc
    if not payload:
        raise AssetError(f"载荷 {payload_path} 是 0 字节", hint="新对象的字节不可能为空")
    want = document.get("payload_sha256")
    if want is not None:
        got = hashlib.sha256(payload).hexdigest()
        if not isinstance(want, str) or not got.startswith(want.strip().lower()):
            raise AssetError(
                f"载荷 sha256 与规格里那一格不符：实算 {got[:16]}… / 规格写 {want!r}",
                hint="改规格或换载荷；⛔ 把 sha 那一行删掉当没看见不算数",
            )
    return AddSpec(
        path_id=int(path_id),
        class_id=None if class_id is None else int(class_id),
        type_id=None if type_id is None else int(type_id),
        payload=bytes(payload),
        payload_source=payload_path,
        name=str(document.get("name") or ""),
        type_name=str(document.get("type_name") or ""),
    )


def _spec_int(document: Mapping[str, Any], key: str, *, what: str) -> int | None:
    value = document.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssetError(
            f"规格文件的 {key} 必须是整数（{what}），拿到 {type(value).__name__}"
            "：布尔不算整数（它是 0/1，会静默指到别的东西上）"
        )
    return int(value)


def _metadata_of(bundle: str | Path) -> Any:
    """目标包的元数据区（**只读**：ufs 取区间 + metadata_writer 解析）。"""
    from ba_mod_maker.asset_kernel import metadata_writer as mw
    from ba_mod_maker.core.asset import ufs

    with ufs.UfsReader(bundle) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        meta_blob = bytes(reader.read_range(node.offset, info.data_offset))
    return mw.parse_serialized_metadata(meta_blob)


def read_type_class_ids(bundle: str | Path) -> tuple[int, ...]:
    """目标包类型表的**逐项 classID**（下标就是 `NewObject.type_id` 要的那个数）。

    ⛔ 读的只是**类型表**，与对象内容无关。类型树的解析器全仓只有一份（`clone.read_type_nodes`），
    这里不新写一套。
    """
    return tuple(int(entry.class_id) for entry in _metadata_of(bundle).types)


def read_object_nodes(bundle: str | Path, type_id: int) -> tuple[Any, Any]:
    """目标包**第 `type_id` 棵类型树**的 `(节点, ref 类型池)`（`clone` 那两个入参的形状）。"""
    from ba_mod_maker.asset_kernel import clone

    meta = _metadata_of(bundle)
    if not 0 <= int(type_id) < len(meta.types):
        raise AssetError(
            f"目标包的类型表只有 {len(meta.types)} 项，没有下标 {type_id}",
            hint="type_id 是**类型表下标**（不是 classID）；给 class_id 让本命令去认",
        )
    tree = meta.types[int(type_id)].type_tree
    common = clone.unitypy_common_strings()
    head = struct.pack("<ii", int(tree.node_count), int(tree.string_buffer_size))
    nodes = clone.read_type_nodes(head + tree.nodes + tree.strings, common_strings=common)
    return nodes, clone.ref_type_pool(meta, common_strings=common)


def resolve_type_id(bundle: str | Path, spec: AddSpec) -> int:
    """规格 -> 目标包里那个**类型表下标**（给了 `type_id` 就用它，但要落在量程内）。"""
    class_ids = read_type_class_ids(bundle)
    if spec.type_id is not None:
        if not 0 <= spec.type_id < len(class_ids):
            raise AssetError(
                f"规格里的 type_id={spec.type_id} 超出目标包的类型表（共 {len(class_ids)} 项）",
                hint="type_id 是类型表下标，每个包都不一样；换 class_id 更稳",
            )
        return int(spec.type_id)
    hits = [index for index, cid in enumerate(class_ids) if cid == spec.class_id]
    if not hits:
        raise AssetError(
            f"目标包的类型表里没有 classID={spec.class_id}",
            hint="这个包里没有这一类对象：要么换包，要么在规格里点名 type_id（它确实在的话）",
        )
    if len(hits) > 1:
        raise AssetError(
            f"目标包里 classID={spec.class_id} 有 {len(hits)} 棵类型树（下标 {hits}）—— 必须先收敛",
            hint="MonoBehaviour 就是这种（每个脚本一棵树）；在规格里点名 type_id，⛔ 不猜",
        )
    return int(hits[0])


def resolve_add_spec(bundle: str | Path, spec: AddSpec) -> AddSpec:
    """把规格里的 `class_id` 认成 `type_id`，并把载荷**自证**过一遍（fail-closed）。"""
    import dataclasses

    type_id = resolve_type_id(bundle, spec)
    nodes, pool = read_object_nodes(bundle, type_id)
    from ba_mod_maker.asset_kernel import clone

    try:
        again = clone.roundtrip(spec.payload, nodes, ref_types=pool)
    except BaModMakerError as exc:
        raise AssetError(
            f"载荷解不出来（用它自己那棵类型树）：{exc.message}",
            hint="载荷必须是**这个类型**的序列化字节；⛔ 拿别的类的字节顶上来，包会坏",
        ) from exc
    if again != spec.payload:
        raise AssetError(
            f"载荷往返不是逐字节相同（{len(spec.payload):,} → {len(again):,} B）",
            hint="这份字节不满足「读出来能原样写回去」⇒ 写进对象表会读坏，本命令不许写",
        )
    return dataclasses.replace(spec, type_id=int(type_id))


# --------------------------------------------------------------------------- #
# 真写：把一条改动落成字节（走 writer 那条链；⛔ 不在这里另写一套写回）
# --------------------------------------------------------------------------- #
def write_edit(
    *,
    bundle: str | Path,
    edit: AssetEdit,
    steps: Sequence[Step] | None = None,
    spec: AddSpec | None = None,
    catalog_path: str | Path | None = None,
    game_root: str | Path | None = None,
    project_root: str | Path | None = None,
    patch_label: str = "",
) -> Any:
    """**真写**一条改动，返回 `writer.WriteReport`（写后复核的读数都在它里面）。

    * `set`    —— 重编码那个对象：**等长**走 `ObjectChange`（原地覆盖），**变长**走 `ObjectGrowth`
      （搬到 node[0] 尾部、8 字节对齐）。⛔ 两条路的判据不同，所以这里量的是**实际长度**；
    * `add`    —— `NewObject`（对象表插条目 + payload 追加尾部）；
    * `delete` —— `writer.write_removals`（`ObjectRemoval` 那条链，⛔ 与 Δ 不是同一条）。
    """
    from ba_mod_maker.asset_kernel import reverse_delta, writer

    root = Path(project_root) if project_root else Path.cwd()
    game = Path(game_root) if game_root else None
    path_id = int(edit.ref.path_id or 0)
    if not path_id:
        raise AssetError(f"{edit.ref} 没有 pathID：三个动词动的都是包里的某个对象")
    common: dict[str, Any] = {
        "project_root": root,
        "game_root": game,
        "catalog_path": Path(catalog_path) if catalog_path else None,
        "patch_store": reverse_delta.PatchStore(
            reverse_delta.patch_store_root(root), game_root=game
        ),
        "patch_label": patch_label or edit.summary,
    }
    if edit.kind == "delete":
        return writer.write_removals(
            bundle, [writer.ObjectRemoval(path_id=path_id)], dry_run=False, **common
        )
    if edit.kind == "add":
        if spec is None or spec.type_id is None:
            raise AssetError("新增对象缺那份规格（type_id 认不出来）—— 不该走到这里")
        change = writer.DeltaChange(
            added=(
                writer.NewObject(
                    path_id=path_id,
                    type_id=int(spec.type_id),
                    payload=bytes(edit.new),
                    name=spec.name,
                    type_name=spec.type_name,
                ),
            )
        )
        return writer.write_delta(bundle, change, dry_run=False, **common)
    if not steps:
        raise AssetError(f"{edit.kind} 要一条字段路径（改的是哪一跳）")
    original, payload, name = reencode_object(bundle, path_id, steps, edit.new)
    if len(payload) == len(original):
        change = writer.DeltaChange(
            inplace=(writer.ObjectChange(path_id=path_id, payload=payload, name=name),)
        )
    else:
        change = writer.DeltaChange(
            growths=(writer.ObjectGrowth(path_id=path_id, payload=payload, name=name),)
        )
    return writer.write_delta(bundle, change, dry_run=False, **common)


def reencode_object(
    bundle: str | Path, path_id: int, steps: Sequence[Step], value: Any
) -> tuple[bytes, bytes, str]:
    """读那个对象 -> 改一跳 -> 重编码；返回 `(原字节, 新字节, 名字)`。

    两条守卫都在这里：

    1. **往返自证**：把**读出来的树原样写回去**必须与磁盘上的字节逐字节相同 ——
       不然这个对象的序列化不可复现，改它会连带改坏别的字段（⛔ 不写）；
    2. 长度按**实际**算：调用方据此在 `ObjectChange`（等长）与 `ObjectGrowth`（变长）之间选，
       ⛔ 不按"应该等长"想当然。
    """
    from ba_mod_maker.asset_kernel import unitypy_source

    with unitypy_source.open_source(bundle) as source:
        obj = source.object_at(int(path_id))
        if obj is None:
            raise AssetError(f"{Path(bundle).name} 里没有 pathID={path_id} 这个对象")
        original = bytes(obj.get_raw_data())
        tree = obj.read_typetree()
        if bytes(obj.save_typetree(tree)) != original:
            raise AssetError(
                f"pathID={path_id} 的 typetree 往返不是逐字节相同",
                hint="这个对象的序列化不可复现 ⇒ 改它会连带改坏别的字段，本命令不许写",
            )
        new_tree = assign_field(tree, steps, value)
        payload = bytes(obj.save_typetree(new_tree))
        name = str(new_tree.get("m_Name") or "") if isinstance(new_tree, Mapping) else ""
    return original, payload, name


def catalog_path_for(args: argparse.Namespace) -> Path | None:
    """`--write` 用：catalog.json 的落点。

    `--catalog` 给了就必须存在（⛔ 不静默跳过成对更新）；没给就按 `--game`（再不行自动找游戏根）
    推一个 —— 推出来的那个**不存在**时返回 None（例：目标是自造的件包，不在游戏 catalog 里）。
    """
    raw = getattr(args, "catalog", None)
    if raw:
        path = Path(str(raw))
        if not path.is_file():
            raise AssetError(
                f"--catalog 指的 {path} 不是文件",
                hint="给一个真的 catalog.json；不想同步 CRC 就别给这一格（但 ADR 0007 要求成对）",
            )
        return path
    from ba_mod_maker.core.assets import game as game_mod

    game_root = getattr(args, "game", None)
    root = Path(str(game_root)) if game_root else game_mod.find_game_root()
    if root is None:
        return None
    # ⛔ 目标**不在游戏目录里**时一个字节都不许碰游戏 catalog（例：自造的件包 / 合成件；
    #    它们在 catalog 里本来就没有条目，去查只会把游戏的 catalog 搅进来）。
    try:
        Path(str(args.bundle)).resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    try:
        path = game_mod.catalog_file(root)
    except (OSError, ValueError):
        return None
    return path if path.is_file() else None


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #
def add_parser(sub: Any) -> argparse.ArgumentParser:
    """注册 `object` 子命令（`__main__.py` 只调这一句）。"""
    parser = sub.add_parser(
        "object",
        help="修改 / 新增 / 删除包里的一个对象（object set / add / delete；退出码 0 / 1 / 2）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_subparsers(dest="object_action", required=True)

    p = actions.add_parser(
        "set",
        help="修改包里某个对象的一个字段（旧值从包里读；默认只写 Δ 计划，--write 才落字节）",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_locator(p)
    p.add_argument(
        "--field",
        required=True,
        help="typetree 路径，例 m_SavedProperties.m_TexEnvs[Layer_A97CDC25].second.m_PathID"
        "（a.b / a[0] / a[名字] 三种写法；对不上会印出可用的键）",
    )
    p.add_argument("--value", required=True, help="新值（类型照旧值那一格定）")
    _add_output(p)

    p = actions.add_parser(
        "add",
        help="新增一个对象（载荷与类型来自一份规格文件 JSON）",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--bundle", required=True, help="UnityFS 包（.bundle / data.unity3d）")
    p.add_argument(
        "--from",
        dest="spec",
        required=True,
        help="规格文件（JSON）：path_id ＋ class_id（或 type_id）＋ payload（相对它的载荷文件）",
    )
    _add_output(p)

    p = actions.add_parser(
        "delete",
        help="删除包里的一个对象（pathID 不在包里就报错退出，不静默成功）",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_locator(p)
    _add_output(p)

    return parser


def _add_locator(parser: argparse.ArgumentParser) -> None:
    """两个动作共用的落点两格：哪个包、包里哪个对象。"""
    parser.add_argument("--bundle", required=True, help="UnityFS 包（.bundle / data.unity3d）")
    parser.add_argument(
        "--path-id",
        dest="path_id",
        required=True,
        type=int,
        help="对象在包里的 pathID（catalog 的 internalId 末段那一串数字）",
    )


def _add_output(parser: argparse.ArgumentParser) -> None:
    """三个动作共用的输出格（--project-root / --dry-run / --write / --json ＋ 写回那三格）。"""
    parser.add_argument(
        "--project-root",
        help="Δ 计划与补丁柜的工程目录（落 <它>/patches/，默认当前目录）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只算不写（工程目录与包都零写入）")
    parser.add_argument(
        "--write",
        action="store_true",
        help="真写：把这一条改动落成字节（Δ ＋ 反向 Δ 补丁柜 ＋ 成对更新 catalog CRC）",
    )
    parser.add_argument("--game", help="--write 用：游戏根（默认自动找）")
    parser.add_argument(
        "--catalog",
        help="--write 用：catalog.json / catalog.bin"
        "（默认按 --game 推；推出来的不存在就不同步 CRC）"
        "· ⚠ 它**不动** aa/catalog.hash（要两个文件一起管走 catalog crc）",
    )
    parser.add_argument("--patch-label", dest="patch_label", help="--write 用：补丁柜里那条标签")
    parser.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="只输出一个 JSON 文档（人读那份改走 stderr）",
    )


def run(args: argparse.Namespace) -> int:
    """跑一遍。返回退出码（0 / 1 / 2）。"""
    emitter = Emitter(json_mode=bool(getattr(args, "json_out", False)))
    verb = str(getattr(args, "object_action", "") or "")
    if verb not in VERBS:
        emitter.failure(f"不认识的动作 {verb!r}", hint=f"这一刀只有 {' / '.join(VERBS)} 三个")
        return EXIT_UNUSABLE

    bundle = Path(str(args.bundle))
    if not bundle.is_file():
        emitter.failure(
            f"打不开包 {bundle}",
            hint="--bundle 给的是一个 UnityFS 容器（.bundle / data.unity3d）",
        )
        return EXIT_UNUSABLE
    dry_run = bool(getattr(args, "dry_run", False))
    write = bool(getattr(args, "write", False))
    if dry_run and write:
        emitter.failure(
            "--dry-run 与 --write 不能同时给（一个字节都不写 vs 真写）",
            hint="先跑一次 --dry-run 看一眼判据，确认了再单独跑一次 --write",
        )
        return EXIT_UNUSABLE
    project_root = _project_root(args)
    field = getattr(args, "field", None)
    path_id = int(getattr(args, "path_id", 0) or 0)
    spec: AddSpec | None = None
    steps: tuple[Step, ...] | None = None
    catalog: Path | None = None

    try:
        if verb == "add":
            spec = resolve_add_spec(bundle, read_add_spec(args.spec))
            path_id = spec.path_id
        elif verb == "set":
            steps = parse_field_path(str(field or ""))
        if write:
            catalog = catalog_path_for(args)
        ref, baseline = _target(bundle, path_id, verb=verb, field=field, steps=steps)
        plan = build_plan(
            verb=verb,
            ref=ref,
            baseline=baseline,
            value=getattr(args, "value", None),
            payload=spec.payload if spec is not None else None,
        )
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    except OSError as exc:
        emitter.failure(f"读不了 {bundle}：{exc}")
        return EXIT_UNUSABLE

    if plan.is_empty:
        emitter.field("包", bundle)
        emitter.field("对象", f"pathID={path_id}")
        emitter.field("结果", "没有改动（新值与旧值相同，账本里不留一条）")
        emitter.document(
            {
                "verb": verb,
                "bundle": str(bundle),
                "path_id": path_id,
                "changed": False,
                "plan": plan_document(plan),
                "plan_path": None,
                "dry_run": dry_run,
            }
        )
        return EXIT_NOTHING

    report: Any = None
    if write:
        try:
            report = write_edit(
                bundle=bundle,
                edit=plan.edits()[0],
                steps=steps,
                spec=spec,
                catalog_path=catalog,
                game_root=getattr(args, "game", None),
                project_root=project_root,
                patch_label=str(getattr(args, "patch_label", "") or ""),
            )
        except (BaModMakerError, OSError) as exc:
            emitter.failure(f"写回失败：{exc}", hint=f"{bundle}（走到这一步之前写回没动手）")
            return EXIT_UNUSABLE

    plan_path: Path | None = None
    if not dry_run:
        try:
            plan_path = write_plan(plan, project_root=project_root)
        except (BaModMakerError, OSError) as exc:
            emitter.failure(f"写不出 Δ 计划：{exc}", hint=f"工程目录：{project_root}")
            return EXIT_UNUSABLE

    _report(emitter, plan=plan, verb=verb, bundle=bundle, path_id=path_id, plan_path=plan_path)
    if report is not None:
        for line in report.report_lines():
            emitter.line(f"  {line}")
    emitter.document(
        {
            "verb": verb,
            "bundle": str(bundle),
            "path_id": path_id,
            "field": field,
            "changed": True,
            "plan": plan_document(plan),
            "plan_path": str(plan_path) if plan_path is not None else None,
            "dry_run": dry_run,
            "write": None if report is None else _write_document(report),
        }
    )
    return EXIT_DONE


def _write_document(report: Any) -> dict[str, Any]:
    """`--write` 那一趟的机器可读读数（只留标量：`verify` 那几格可能带嵌套值）。"""
    verify = report.verify if isinstance(report.verify, Mapping) else {}
    return {
        "wrote_bundle": bool(report.wrote_bundle),
        "wrote_catalog": bool(report.wrote_catalog),
        "patch": str(report.patch) if report.patch else None,
        "patch_bytes": int(report.patch_bytes),
        "verify_ok": {
            str(key): bool(value.get("ok")) if isinstance(value, Mapping) else bool(value)
            for key, value in verify.items()
        },
    }


def _project_root(args: argparse.Namespace) -> Path:
    raw = getattr(args, "project_root", None)
    return Path(str(raw)) if raw else Path.cwd()


def _target(
    bundle: Path,
    path_id: int,
    *,
    verb: str,
    field: str | None,
    steps: Sequence[Step] | None = None,
) -> tuple[AssetRef, Callable[[AssetRef], Any]]:
    """把 (包, pathID) 变成 [AssetRef] ＋ 一条**只读**的基线。

    基线是「这一处现在是什么」的唯一来源（`AssetChangeset` 的三条契约）：这里给的是一次
    读快照的闭包 —— 它**不写**任何东西，也不会在撤销时被再问一次。

    * `set`    —— 基线是**那一格现在的值**（从 typetree 里读出来）；
    * `add`    —— 基线回答「这一处**有没有**」：没有给 [MISSING]（`add_object` 的守卫要它）；
      已经占着就把旧载荷交出去，于是守卫当场报「pathID 不许撞」；
    * `delete` —— 基线是那个对象的**整份字节**（撤销的承诺是回到它）。
    """
    if verb == "set":
        if not field:
            raise AssetError("set 要 --field（typetree 路径）")
        walked = tuple(steps) if steps is not None else parse_field_path(field)
        tree = read_typetree(bundle, path_id)
        return AssetRef(bundle, path_id, FieldPath(field)), _tree_baseline(tree, walked)

    payload = read_object_bytes(bundle, path_id)
    if verb == "add":
        return AssetRef(bundle, path_id), _presence_baseline(payload)

    if payload is None:
        raise AssetError(
            f"{bundle.name} 里没有 pathID={path_id} 这个对象，删不了",
            hint="pathID 抄错了？也可能它本来就在另一个包里 —— 这条命令不会静默成功",
        )
    return AssetRef(bundle, path_id), _payload_baseline(payload)


def _tree_baseline(tree: Mapping[str, Any], steps: Sequence[Step]) -> Callable[[AssetRef], Any]:
    def baseline(_ref: AssetRef) -> Any:
        return resolve_field(tree, steps)

    return baseline


def _payload_baseline(payload: bytes) -> Callable[[AssetRef], Any]:
    def baseline(_ref: AssetRef) -> Any:
        return payload

    return baseline


def _presence_baseline(existing: bytes | None) -> Callable[[AssetRef], Any]:
    """新增动词的基线：**没有**这一处时返回 [MISSING]；已经占着就把它交出去（守卫当场报错）。"""
    def baseline(_ref: AssetRef) -> Any:
        return MISSING if existing is None else existing

    return baseline


def _report(
    emitter: Emitter,
    *,
    plan: AssetPlan,
    verb: str,
    bundle: Path,
    path_id: int,
    plan_path: Path | None,
) -> None:
    """人读那几行：**一次动作只报一条判据**（`AssetEdit.summary` 是账本的同一份话术）。"""
    emitter.field("包", bundle)
    emitter.field("动作", _VERB_LABELS.get(verb, verb))
    emitter.field("对象", f"pathID={path_id}" + ("（新）" if verb == "add" else ""))
    edit = plan.edits()[0]
    if verb == "set":
        emitter.field("字段", edit.ref.field)
        emitter.field("旧值", _short(edit.old))
        emitter.field("新值", _short(edit.new))
    elif verb == "add":
        emitter.field("载荷", f"{len(edit.new or b'')} 字节  sha256 {_sha16(edit.new)}")
    else:
        emitter.field("载荷", f"{len(edit.old or b'')} 字节  sha256 {_sha16(edit.old)}")
    emitter.line(f"[判据] {edit.summary}")
    if plan_path is None:
        emitter.field("计划", "（--dry-run：没有写盘）")
    else:
        emitter.field("计划", f"{plan_path}（{plan.count} 条）")


#: 三个动词给用户看的那个名字（报错话术与读数共用一份）。
_VERB_LABELS: Final[dict[str, str]] = {"set": "修改", "add": "新增", "delete": "删除"}


def _sha16(value: Any) -> str:
    """载荷的 sha256 前 16 位（⛔ 不印整串：读数一行要能扫）。"""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return hashlib.sha256(bytes(value)).hexdigest()[:16]
    return "—"


def _short(value: Any, limit: int = 120) -> str:
    """人读一行：字节只报长度，长文本截断。"""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"{len(value)} 字节"
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"
