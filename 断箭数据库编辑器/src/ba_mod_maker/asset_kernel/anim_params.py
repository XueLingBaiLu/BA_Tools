r"""L11 · `AnimationManager` 的**参数族**（整族读 / 整族改）—— 纯函数，一个字节都不写盘。

## 这一族住在哪（逐条带出处）

`AnimationManager` 是挂在单位 prefab 上的 MonoBehaviour
（`BrokenArrow|BrokenArrow.Client.Ecs.Animations|AnimationManager`，152 个类型树节点）：

    AnimationManager
      └ _container        (BoneContainer，内联类，无 [SerializeReference])
          └ _settings     (BoneSettings，内联类)   <-- 本模块动的**只有**这里
              ├ WheelSpeed          float
              ├ SuspensionSpeed     float
              ├ TracksSpeed         float
              ├ TurnSpeedMod        float
              ├ BodyShake           float
              ├ BodyShakeAggressive float
              └ BodyMass            float

形状出处 = `asset_kernel/unit_script_shapes.py` 的 `...|AnimationManager` 那一项
（**生成物**，重取走 `tools/gen_script_shapes.py`）—— 本模块**照抄**那 7 个名字与顺序，
⛔ 不另立第二份；`test_anim_params.py` 拿它逐条对拍。

## 为什么偏偏是这 7 个

游戏每帧拿它们算位移 / 转向 / 抖动（`BoneContainer.Update` → `Wheels.Move` /
`Tracks.Move` / `Suspension.Move`），而且 `[SerializeField]` **序列化进 prefab**
⇒ 「改它 = 改行为」且**不用重打动画**。
履带那条尤其要紧：`Tracks.Move` 每帧调 `Material.set_mainTextureOffset`，
所以往材质里写 UV offset 会被运行期**盖掉**（`_work/animdrive/probe_track_uv.py` 的读数）
—— 数据侧**唯一**的旋钮就是 `TracksSpeed`。

## 判据一行

    ba-mod-maker anim params --bundle <包> --path-id <AnimationManager 的 pathID>
      -> 印 7 个名字 + 现值 + 它们在类型树里的路径；一个字节都不写
    ba-mod-maker anim params --bundle <包> --path-id <N> --set TracksSpeed=5.0
      -> 「改了 1 格」+ 干跑零写入；加 --write 才落字节，且**写后读回**再印一次

## 正控 / 负控

* **正控**：真件 T-72B 的 `AnimationManager` —— `TracksSpeed == 2.0`；读出来的 7 个值与
  `_work/l11/params.tsv` 逐条相同，也与 `unit_script_shapes.py` 的名字表逐条相同；
* **负控 1**：不给 `--set` ⇒ **逐位相同**（干跑零写入 ＋ 对象字节逐字节相同）；
* **负控 2**：写一个**不在族里**的名字（例 `TrackSpeed` 少个 s）⇒ 当场拒、点名那 7 个；
* **负控 3**：值不是数（例 `TracksSpeed=fast`）⇒ 当场拒。

## ⛔ 本模块不碰的东西

* 只写 `_container._settings` 下那 7 个 float —— `UseContainer` / `_root` / `_body` /
  `_traks` / `_wheels` / `_suspension` / `_acceleration` / `_recoils` **一个都不碰**；
* 全 0 与负数都是**合法**取值（真件实测 `TracksSpeed` 有 `-2.0` 8 例）⇒ ⛔ 不做取值范围闸，
  只在读数里如实印出来；
* ⛔ 没有「换」这个动作：本模块只有「**改**一个既有字段的值」（AGENTS §0.10 的三种原子操作之一）。
"""

from __future__ import annotations

import copy
import math
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ba_mod_maker.core.errors import AssetError

#: 这一族的名字（`m_Name` 口径）：`<类>.<内联类>.<内联类>`。
FAMILY = "AnimationManager._container._settings"
#: 走到 `BoneSettings` 的那一跳（从 `AnimationManager` 的根往下）。
SETTINGS_STEPS: tuple[str, ...] = ("_container", "_settings")

#: 那 7 个 float —— **顺序与名字逐字照抄** `unit_script_shapes.py` 的 AnimationManager 项。
PARAM_ORDER: tuple[str, ...] = (
    "WheelSpeed",
    "SuspensionSpeed",
    "TracksSpeed",
    "TurnSpeedMod",
    "BodyShake",
    "BodyShakeAggressive",
    "BodyMass",
)
#: 只认 float（真件 7/7 都是 float；出现别的类型当场拒，⛔ 不猜）。
PARAM_KIND = "float"


class AnimParamsError(AssetError):
    """这一族参数上的「不许猜」：名字不在族里 / 值不是数 / 树上没有 `_settings`。"""


def settings_of(tree: object) -> Mapping[str, Any]:
    """从 `AnimationManager` 的字段树里取到 `_container._settings`（取不到就**点名拒**）。"""
    node: object = tree
    trail: list[str] = []
    for step in SETTINGS_STEPS:
        if not isinstance(node, Mapping):
            raise AnimParamsError(
                f"{FAMILY}：走到 {'.'.join(trail) or '<根>'} 时不是一个对象"
                " —— 拿错对象了？（要给挂在 prefab 上的 AnimationManager 那个 MonoBehaviour）",
                hint="给 AnimationManager 的 pathID；`object set` 那条通用手也能先读一眼",
            )
        if step not in node:
            raise AnimParamsError(
                f"{FAMILY}：这个对象上没有 {'.'.join([*trail, step])}"
                " —— 它不是 AnimationManager，或者那份 prefab 的字段形状变了",
                hint="先跑 `anim params --bundle <包> --path-id <N>`（不带 --set）看一眼它有什么",
            )
        trail.append(step)
        node = node[step]
    if not isinstance(node, Mapping):
        raise AnimParamsError(f"{FAMILY}：{' '.join(trail)} 不是一个对象")
    return node


def parse_change(text: str) -> tuple[str, float]:
    """`"TracksSpeed=5.0"` -> `("TracksSpeed", 5.0)`。⛔ 名字不在族里 / 值不是数都当场拒。"""
    name, sep, raw = str(text).partition("=")
    name = name.strip()
    if not sep or not name:
        raise AnimParamsError(
            f"--set 要写成 名字=值（收到 {text!r}）",
            hint="例：--set TracksSpeed=5.0（可重复给，一次改一整族）",
        )
    return name, parse_value(name, raw)


def float32(value: object) -> float:
    """把值**量化成 float32** —— 这 7 格在包里就是 4 字节浮点（`BoneSettings` 的 `float`）。

    ⚠ 这条是**判据**不是修饰：`0.45` 在 float32 里是 `0.44999998807907104`，
    不量化就会出现「写进去 0.45、读回来 0.45，而两个数 `!=`」那种假红（2026-10-17 实测踩过）。
    超出 float32 范围（例 1e40）⇒ 拒，⛔ 不静默变 inf。
    """
    try:
        return struct.unpack("<f", struct.pack("<f", float(value)))[0]
    except (OverflowError, struct.error, TypeError, ValueError) as exc:
        raise AnimParamsError(
            f"值 {value!r} 超出 float32 的范围（Unity 这 7 格是 4 字节浮点）"
        ) from exc


def same_value(one: object, other: object) -> bool:
    """两个值在 **float32 口径**下算不算同一个（`None` 只与 `None` 相同）。"""
    if one is None or other is None:
        return one is other
    try:
        return float32(one) == float32(other)
    except AnimParamsError:
        return one == other


def parse_value(name: str, raw: object) -> float:
    """把一格的新值转成 float32；名字不在族里 / 不是有限数 / 超范围 ⇒ 点名拒。"""
    key = str(name).strip()
    if key not in PARAM_ORDER:
        raise AnimParamsError(
            f"{key!r} 不在 {FAMILY} 这一族里（这一族只有 {len(PARAM_ORDER)} 个）",
            hint="认得的是：" + " / ".join(PARAM_ORDER),
        )
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise AnimParamsError(f"{key} 的值要一个数，收到 {raw!r}")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise AnimParamsError(f"{key} 的值 {raw!r} 不是数") from exc
    if not math.isfinite(value):
        raise AnimParamsError(f"{key} 的值 {raw!r} 不是有限数（⛔ 不许写 nan / inf 进包）")
    # ★ 一律量化成 float32：这样 `after` 里那个数**就是包里那个数**，
    #   写后读回才可能真的逐格相同（见 float32() 的说明）。
    return float32(value)


def parse_changes(texts: Iterable[str]) -> dict[str, float]:
    """一串 `名字=值` -> `{名字: 值}`；同一个名字给两遍 ⇒ 拒（⛔ 不静默取最后一个）。"""
    out: dict[str, float] = {}
    for text in texts:
        name, value = parse_change(text)
        if name in out:
            raise AnimParamsError(f"--set 里 {name} 给了两遍（{out[name]} / {value}）")
        out[name] = value
    return out


def parse_document(payload: object) -> dict[str, float]:
    """`--from-json` 那份文档：`{"TracksSpeed": 5.0, ...}`（整族一次给）。"""
    if not isinstance(payload, Mapping):
        raise AnimParamsError('--from-json 要一个 JSON 对象：{"TracksSpeed": 5.0}')
    return {str(key): parse_value(str(key), value) for key, value in payload.items()}


@dataclass(frozen=True, slots=True)
class FamilyReading:
    """一次**纯读**的读数：7 个名字 + 现值（缺的记 None，⛔ 不拿 0 顶上）。"""

    path_id: int
    source: str
    values: tuple[tuple[str, float | None, str], ...]

    def get(self, name: str) -> float | None:
        """一个名字的现值（不在族里 ⇒ 抛；族里但树上缺 ⇒ None）。"""
        if name not in PARAM_ORDER:
            raise AnimParamsError(f"{name!r} 不在 {FAMILY} 这一族里")
        for key, value, _kind in self.values:
            if key == name:
                return value
        return None

    @property
    def present(self) -> int:
        """树上真有值的名字数（真件应当是 7）。"""
        return sum(1 for _n, value, _k in self.values if value is not None)

    @property
    def missing(self) -> tuple[str, ...]:
        """树上没有（或类型不对）的名字 —— 如实报出来，⛔ 不补默认值。"""
        return tuple(name for name, value, _k in self.values if value is None)

    def document(self) -> dict[str, Any]:
        """`--json` 那一份（人读那份与它同源）。"""
        return {
            "family": FAMILY,
            "path_id": int(self.path_id),
            "source": str(self.source),
            "present": self.present,
            "missing": list(self.missing),
            "values": {name: value for name, value, _k in self.values},
            "paths": {name: "".join(f"{step}." for step in SETTINGS_STEPS) + name
                       for name in PARAM_ORDER},
        }

    def lines(self, *, title: str = "") -> tuple[str, ...]:
        """人读那几行（面板 / CLI / 测试读同一批字符串）。"""
        head = (
            f"{title}{FAMILY}：{self.present}/{len(PARAM_ORDER)} 格有值"
            + (f"（缺 {', '.join(self.missing)}）" if self.missing else "")
        )
        out = [head]
        for name, value, kind in self.values:
            shown = "（树上没有）" if value is None else f"{value:g}"
            out.append(f"    {name} = {shown}   [{kind}]  {'.'.join(SETTINGS_STEPS)}.{name}")
        return tuple(out)


def read_family(tree: object, *, path_id: int = 0, source: str = "") -> FamilyReading:
    """整族**纯读**（7 个名字一个不落；树上缺的那个记 None）。"""
    settings = settings_of(tree)
    values: list[tuple[str, float | None, str]] = []
    for name in PARAM_ORDER:
        raw = settings.get(name)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            if raw is not None:
                # 类型不对 ⇒ 如实报「不是 float」，⛔ 不当成 0；读档不抛（只读那半条腿不许装死）
                values.append((name, None, f"{type(raw).__name__}（不是 {PARAM_KIND}）"))
                continue
            values.append((name, None, PARAM_KIND))
            continue
        values.append((name, float(raw), PARAM_KIND))
    return FamilyReading(path_id=int(path_id), source=str(source), values=tuple(values))


def apply_family(tree: Mapping[str, Any], changes: Mapping[str, float]) -> dict[str, Any]:
    """**纯函数**：拿一份 `AnimationManager` 字段树 + `{名字: 新值}` -> 一份新树。

    ⛔ 只改 `_container._settings` 下那 7 个名字；其余键**逐位照抄**（deepcopy 之后再写）。
    ⛔ 空改动 ⇒ 抛（「什么都没改」不该被当成一次成功的写入）。
    """
    if not changes:
        raise AnimParamsError("一个 --set 都没给 —— 空改动不写（⛔ 不把它当成写成功）")
    normalized = {
        str(name).strip(): parse_value(name, value) for name, value in changes.items()
    }
    _ = settings_of(tree)  # 先自证这棵树长得对（⛔ 不在错的树上写）
    out = copy.deepcopy(dict(tree))
    settings = settings_of(out)
    for name, value in normalized.items():
        settings[name] = value
    return out


def changed_params(
    before: FamilyReading, after: FamilyReading
) -> tuple[tuple[str, float | None, float | None], ...]:
    """两次读数之间**变了的**那些格（判据：改 1 格就只该有 1 条）。"""
    out: list[tuple[str, float | None, float | None]] = []
    for name in PARAM_ORDER:
        old, new = before.get(name), after.get(name)
        if not same_value(old, new):
            out.append((name, old, new))
    return tuple(out)


def unchanged_paths(before: object, after: object, *, limit: int = 12) -> tuple[str, ...]:
    """两棵树之间**变了的叶子路径**（只该落在 `_container._settings.<改了的名字>` 里）。"""
    out: list[str] = []

    def walk(one: object, two: object, trail: str) -> None:
        if len(out) >= limit:
            return
        if isinstance(one, Mapping) and isinstance(two, Mapping):
            for key in list(one) + [k for k in two if k not in one]:
                walk(one.get(key), two.get(key), f"{trail}.{key}" if trail else str(key))
            return
        if isinstance(one, Sequence) and isinstance(two, Sequence) and not isinstance(one, str):
            if len(one) != len(two):
                out.append(trail)
                return
            for index, (a, b) in enumerate(zip(one, two, strict=True)):
                walk(a, b, f"{trail}[{index}]")
            return
        if one != two:
            out.append(trail)

    walk(before, after, "")
    return tuple(out)


__all__ = [
    "FAMILY",
    "PARAM_KIND",
    "PARAM_ORDER",
    "SETTINGS_STEPS",
    "AnimParamsError",
    "FamilyReading",
    "apply_family",
    "changed_params",
    "float32",
    "parse_change",
    "parse_changes",
    "parse_document",
    "parse_value",
    "read_family",
    "same_value",
    "settings_of",
    "unchanged_paths",
]
