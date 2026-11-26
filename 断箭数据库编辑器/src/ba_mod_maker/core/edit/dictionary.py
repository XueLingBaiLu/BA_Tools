"""字段词典：中文名 / 作用说明 / 枚举与位掩码解码（零 Qt）。

数据来自产品自带的 core/fields/*.json（口径见那里的 README）：
**产品知识随产品走** —— 用户直接打开 BA_Mod.zip 时旁边没有词典可读。

两条分工（README 钉死的）：

- **取值优先查 field_enums**（枚举 / 掩码 / 布尔）；
- **中文名与说明优先查 field_labels**，那边没有才用 field_enums 的 cn；
- 两边都没有的列：显示**英文列名**，并且 FieldInfo.missing 为真 ——
  界面据此把它当"待补的口子"，而不是假装它有中文名。

## 界面拿它做什么

| 问它什么 | 得到什么 |
|---|---|
| 这一格的中文名 | field_info().cn |
| 悬浮注释 | field_info().doc |
| 值显示成中文 | decode(info, value)（掩码逐位拼） |
| 该给什么控件 | field_info().control（数字 / 文本 / 开关 / 下拉 / 勾选 / 外键搜索 / 资源键） |
| 原始值备查 | decode 返回的 Decoded.raw_text —— 掩码与"没登记的值"都要留 |
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from ba_mod_maker.core.asset.tables import resolve_table_name
from ba_mod_maker.core.edit.refs import display_table

#: 词典目录（core/fields/）。
FIELD_DIR = Path(__file__).resolve().parent.parent / "fields"

#: 控件种类。界面按它选控件，**不在界面里再判一次类型**。
CONTROL_NUMBER = "number"
CONTROL_TEXT = "text"
CONTROL_BOOL = "bool"
CONTROL_ENUM = "enum"
CONTROL_MASK = "mask"
CONTROL_FOREIGN = "foreign"
CONTROL_RESOURCE = "resource"

#: 列名的后缀 -> 它是"游戏包里的资源键"。
#: 实测（docs/数据-表与列清单.md + 卡片生成器）：这些列存的是 ICON_* / US_Rangers
#: 这类**文件名键**，不是可计算的数，也不是枚举。
_RESOURCE_SUFFIXES: tuple[str, ...] = (
    "FileName",
    "Icon",
    "IconKey",
    "Picture",
    "AudioPreset",
    "PresetName",
)

#: 列名后缀**认不出来**、但值确实是"又长又难记的键"的那几列。
#:
#: | 列 | 值长什么样 | 是什么键 |
#: |---|---|---|
#: | `Options.ThumbnailOverride` | `US_ICV_DRAGON-Label` | 缩略图键（**用户报的那一格**） |
#: | `Options.PortraitOverride` | `DLC2/EESTI_SCOUTS/EESTI_SCOUTS` | 立绘键 |
#: | `Options.AudioPresetOverride` | `UNIT_SOUND_PRESET_B1B_Nuke` | 语音预设键 |
#: | `Specializations.Illustration` | `RU_SPEC_TANK` | 专精插画键（图标族登记表里就有它） |
#: | `Ammunitions.FXPreset` | `AMMUNITION_VFX_PRESET_30mm` | 特效预设键 |
#: | `Modifications.UIName` · `Specializations.UIName` | `Custom_Slot_Armor` | **本地化键** |
#: | `Specializations.UIDescription` | `ui_spec_usmc_name` | 同上，同一个毛病 |
#: | `Options.UIName`（「选项界面名」） | `Custom_Option_40xS8` | 同上（用户 2026-10-02 报的） |
#:
#: 出处：`_work/probe_control_sweep.py`（2026-10-02 全库 24 张表逐列扫**值的形状**）。
#: 两条判据都写在那份脚本里：① 图标族登记表（`core/assets/families.py::FAMILIES[].column`）
#: 里的列**定义上**就是资源键；② 值没有空格、只由 `[A-Za-z0-9_./-]` 组成、且含 _ / - / 全大写
#: —— 命中 ≥90% 且样本 ≥5 条就是键列。**换游戏版本重跑一遍**，它会把漏掉的列打出来。
#:
#: ❗ 为什么不做成"按值形状**动态**判"：那要逐列扫全表，而 `control_for` 是**每一格**都调的
#:    （一个单位上千格、Options 表 2046 行）—— 那样会把卡片刷新拖慢一个数量级。
#:    写死在这里 = 零成本；漏了就由上面那份脚本兜底打出来。
_RESOURCE_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    {
        ("Options", "ThumbnailOverride"),
        ("Options", "PortraitOverride"),
        ("Options", "AudioPresetOverride"),
        ("Specializations", "Illustration"),
        ("Ammunitions", "FXPreset"),
        # 本地化键那几列：值一样是"必须一字不差打出来"的长键（`Custom_Slot_Squad_loadout`），
        # 同一个毛病、同一种修法 —— 候选就是这一列在这张表里用过的键。
        ("Modifications", "UIName"),
        ("Specializations", "UIName"),
        ("Specializations", "UIDescription"),
        # ❗ `Options.UIName`（右栏「选项界面名」，用户 2026-10-02 画红圈那一格）：2046 行非空、
        #    **1578 行翻得出来 = 77%**（其余是 `12x AGM-86C ALCM` 那种明文）、取值 688 种。
        #    它**不是**判据 2 找出来的 —— 那一关看"值的形状"、门槛 90%，这一列只有 77%，
        #    正好被漏掉（`_work/probe_control_sweep.py` 的**判据 3**"翻得出来"才抓到它；
        #    全库就这一列）。同族另外三列（Modifications / Specializations）早就在这张表里。
        ("Options", "UIName"),
    }
)


def _is_resource_column(table: str, column: str) -> bool:
    """这一列存的是不是"键"（资源键 / 本地化键）。两条判据，都有出处：

    1. **列名后缀**（`_RESOURCE_SUFFIXES`）—— 覆盖绝大多数；
    2. **`_RESOURCE_COLUMNS`** —— 实测补上的那几列（名字不带那些后缀，但值就是键）。
    """
    return column.endswith(_RESOURCE_SUFFIXES) or (table, column) in _RESOURCE_COLUMNS


#: 值类型 -> 控件（数字/文本两档）。只在词典没给 kind 时用。
_TYPE_CONTROL: dict[str, str] = {
    "int": CONTROL_NUMBER,
    "float": CONTROL_NUMBER,
    "bool": CONTROL_BOOL,
    "text": CONTROL_TEXT,
    "unknown": CONTROL_TEXT,
}

#: 词典 kind -> 控件。other 表示"整数但不是枚举"，所以不覆盖取值类型判断。
_KIND_CONTROL: dict[str, str] = {
    "enum": CONTROL_ENUM,
    "mask": CONTROL_MASK,
    "bool": CONTROL_BOOL,
}


@dataclass(frozen=True, slots=True)
class Decoded:
    """一个值的中文形态。

    text 给人看；raw_text 是原始值（掩码要留、没登记的值也要留）；
    kind 是词典给的 kind（enum / mask / bool / other / text）。
    """

    text: str
    raw_text: str
    kind: str
    #: 掩码逐位解出来的中文（没有就是空元组）。
    bits: tuple[str, ...] = ()
    #: 值落在 values 里没有 —— 界面要如实显示"没登记"，不编。
    unknown: bool = False


@dataclass(frozen=True, slots=True)
class FieldInfo:
    """一列的词典信息 + 该给什么控件。"""

    table: str
    column: str
    cn: str
    doc: str
    kind: str
    control: str
    values: Mapping[str, str]
    evidence: str
    #: 指向哪张表（外键才有）。
    foreign: str | None = None
    #: 词典里查不到这一列（中文名回落成英文列名）。
    missing: bool = False

    @property
    def is_foreign(self) -> bool:
        return self.foreign is not None

    @property
    def assumed(self) -> bool:
        """词典自己标了「强假设」—— 界面可以据此加一句小字。"""
        return self.evidence == "强假设"


def _read(name: str) -> dict:
    path = FIELD_DIR / name
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:  # pragma: no cover - 产品包里必有
        return {}
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=1)
def field_labels() -> dict:
    """field_labels.json（24 表 391 列的中文名与说明）。"""
    return _read("field_labels.json")


@lru_cache(maxsize=1)
def field_enums() -> dict:
    """field_enums.json（159 列的枚举 / 掩码 / 布尔值域）。"""
    return _read("field_enums.json")


def _dictionary_key(table: str) -> str:
    """表名 -> **词典 JSON 的键**。查词典前必须先过这一步。

    两张词典 JSON（`core/fields/*.json`）是**数据侧交付物**，键用数据集侧那一套叫法
    （那张改过名的表在那里叫 `PlaneFlyPresets`），而实例交出来的表名是**规范名**
    `FlyPresets`（`core/data/loader.py` 一处归一）。不归一的话这张表的列会退化成
    英文列名、说明与枚举全丢 —— 实测（2026-10-01）：`field_info("FlyPresets", "MaxSpeed").cn`
    得到 `"MaxSpeed"` 而不是「最大速度」（外部库来源一直是这个症状，归一之后目录来源也一样）。

    归一交给 `core/asset/tables.py::resolve_table_name` 的别名表 ——
    `app/dialogs/dictionary_data.py` 也是这么做的，**别再写第二份映射**。
    """
    return resolve_table_name(table, field_labels().keys()) or table


@lru_cache(maxsize=4096)
def field_info(table: str, column: str) -> FieldInfo:
    """一列的词典条目 + 控件类型。**查不到也返回对象**（cn = 英文列名）。"""
    key = _dictionary_key(table)
    labels = field_labels().get(key)
    label = labels.get(column) if isinstance(labels, dict) else None
    node = field_enums().get(f"{key}.{column}")
    node = node if isinstance(node, dict) else None

    cn = _text(label, "cn") or _text(node, "cn") or column
    doc = _text(label, "doc") or _text(node, "doc")
    kind = _text(node, "kind") or "text"
    raw_values = node.get("values") if node else None
    values: dict[str, str] = (
        {str(k): str(v) for k, v in raw_values.items()} if isinstance(raw_values, dict) else {}
    )

    # ❗ 用 display_table 而不是 foreign_table：前者**包含指向 Units 的列**
    #    （UnitId / ReplaceUnitId）—— 它们一样要在右边显示名字、一样能点 ↗ 跳过去。
    #    后者留给 scope 走图（对指向 Units 的一律 None，见它的 docstring）。
    target = display_table(column)
    if column == "Id":
        control = CONTROL_NUMBER
    elif target is not None:
        control = CONTROL_FOREIGN
    elif kind in _KIND_CONTROL:
        control = _KIND_CONTROL[kind]
    elif _is_resource_column(key, column):
        control = CONTROL_RESOURCE
    else:
        control = CONTROL_TEXT  # 由调用方按取值类型覆写成数字 / 文本

    return FieldInfo(
        table=table,
        column=column,
        cn=cn,
        doc=doc,
        kind=kind,
        control=control,
        values=values,
        evidence=_text(node, "evidence"),
        foreign=target,
        missing=label is None and node is None,
    )


def control_for(info: FieldInfo, column_kind: str = "") -> str:
    """把"取值类型"并进控件判断。

    为什么不让 field_info 自己看数据：它只有词典，没有表
    （词典是产品知识，表是数据）。两者分开，各自可单独断言。

    ❗ **资源键只可能是字符串列**：名字像而类型不是的（例：`Options.StealthOverride` 是
    float）仍旧给数字框 —— 给一个 float 列配"候选下拉"，选一条字符串再写回数字列，
    那是在制造坏数据。判据取自**数据**（column_kind），不是列名。
    """
    if info.control == CONTROL_RESOURCE:
        if _TYPE_CONTROL.get(column_kind, CONTROL_TEXT) == CONTROL_TEXT:
            return CONTROL_RESOURCE
        return _TYPE_CONTROL.get(column_kind, CONTROL_NUMBER)
    if info.control != CONTROL_TEXT:
        return info.control
    if info.kind == "other":
        return _TYPE_CONTROL.get(column_kind, CONTROL_NUMBER)
    return _TYPE_CONTROL.get(column_kind, CONTROL_TEXT)


def decode(info: FieldInfo, value: Any) -> Decoded:
    """把一个值解成中文。

    - **bool**：是 / 否；
    - **enum**：查 values；没登记就原样显示数字，并置 unknown；
    - **mask**：**逐位拼中文**（36 -> 「载具 + 舰船」），原始值留在 raw_text；
    - 其余：原样。
    """
    raw_text = _raw_text(value)
    if info.kind == "bool":
        truth = value if isinstance(value, bool) else raw_text in ("1", "true", "True")
        return Decoded(text="是" if truth else "否", raw_text=raw_text, kind="bool")

    if info.kind == "mask":
        number = _as_int(value)
        if number is None:
            return Decoded(text=raw_text, raw_text=raw_text, kind="mask")
        bits = tuple(
            text
            for key, text in sorted(info.values.items(), key=lambda kv: int(kv[0]))
            if int(key) and number & int(key)
        )
        if not bits:
            # 0 或"每一位都没登记"：如实说，不编一个"无"以外的词。
            zero = info.values.get("0")
            return Decoded(
                text=zero if zero and number == 0 else raw_text,
                raw_text=raw_text,
                kind="mask",
                unknown=number != 0,
            )
        return Decoded(
            text=" + ".join(bits),
            raw_text=raw_text,
            kind="mask",
            bits=bits,
            unknown=bool(number & ~_known_mask(info.values)),
        )

    if info.kind == "enum":
        text = info.values.get(raw_text)
        if text is not None:
            return Decoded(text=text, raw_text=raw_text, kind="enum")
        return Decoded(text=raw_text, raw_text=raw_text, kind="enum", unknown=True)

    return Decoded(text=raw_text, raw_text=raw_text, kind=info.kind or "text")


def _known_mask(values: Mapping[str, str]) -> int:
    mask = 0
    for key in values:
        mask |= _as_int(key) or 0
    return mask


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 10)
        except ValueError:
            return None
    return None


def _raw_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _text(node: Any, key: str) -> str:
    if not isinstance(node, dict):
        return ""
    value = node.get(key)
    return value.strip() if isinstance(value, str) else ""


__all__ = [
    "CONTROL_BOOL",
    "CONTROL_ENUM",
    "CONTROL_FOREIGN",
    "CONTROL_MASK",
    "CONTROL_NUMBER",
    "CONTROL_RESOURCE",
    "CONTROL_TEXT",
    "FIELD_DIR",
    "Decoded",
    "FieldInfo",
    "control_for",
    "decode",
    "field_enums",
    "field_info",
    "field_labels",
]
