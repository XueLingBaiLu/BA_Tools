"""可见性层 —— UserItemsConfig.Skins[单位Id] 的 新增 / 删除 / 修改（产品 API）。

## 这一层收的是哪件事

皮肤有**两处**落点（设计稿 docs/设计-涂装贴图皮肤.md §1）：

| 层 | 落点 | 管什么 |
|---|---|---|
| ③ 外观 | units_*.bundle 里 prefab 的 Skins.Data | 皮肤**长什么样** |
| ④ 可见性 | data.unity3d 的 UserItemsConfig | 菜单里**看不看得见** |

本模块只做 ④；③ 见 asset_kernel/prefab_skins.py。
两层的**唯一连接键是皮肤 Id**：这里写进去的 Id，要在 ③ 那张表里有同号的一条。

## 形状（不是推的）

真件 UserItemsConfig 的序列化字段树（**417 个节点**）来自 prefabsme 包类型表
type_id=233 那一项（Unity 自己写进包的树），冻结在本文件的 _TREE_TEXT 里。
顶层字段实测（顺序就是序列化顺序）：

    m_GameObject / m_Enabled / m_Script / m_Name
    _errors        : List<string>        （两个实例实测都是空表）
    Avatars        : List<UserItemData>
    Patches        : List<UserItemData>
    Frames         : List<UserItemData>
    Icons          : List<UserItemData>
    Epaulets       : List<UserItemData>
    Skins          : SkinGroupDict
                       _keyData   : int[]
                       _valueData : SkinGroupData[]
    SkinPacks      : SkinPackDict   { _keyData, _valueData }
    DLC            : DLCMarketDict  { _keyData, _valueData }
    DLCContainers  : DLCContainer[]

    SkinGroupData      { Data : SkinItemData[] }
    SkinItemData       { Id, Grade, SkinPack, Triggers, HideUnlockInfo,
                         IsDefault, NoneElement, HideIfNotUnlocked,
                         Disabled, Free }
    UnlockTriggersData { Trigger, DescriptionLocKey, Count }

_keyData / _valueData 是**平行数组**（Unity 自定义字典的序列化）：下标一一对应，
单位 Id 在 _keyData、它的皮肤组在 _valueData 同下标。

⚠ 树里**没有** SkinsPlain —— dump.cs 里那个 public 字段不参与序列化。
**树是权威**，别照 dump.cs 的字段清单拼结构。

## 三个动词（AGENTS §0.10：只有 新增 / 删除 / 修改）

| 动词 | 本模块 | 条目数 | 走哪一档 |
|---|---|---|---|
| **新增** | add_skin_item / plan_add | **N -> N+1** | 变长（delta > 0） |
| **删除** | remove_skin_item / plan_remove | **N -> N-1** | 变长（delta < 0） |
| **修改** | update_skin_item / plan_update | 等长 | 等长（原地覆盖） |

⛔ 没有「换」：要换一条皮肤的目标 = **删除旧条 ＋ 新增新条**（两个动作）。

### 触发器（UnlockTriggersData）也是同一套三个动词

一条皮肤上的解锁条件是一个**列表**，元素没有 Id（真件类型树里就
Trigger / DescriptionLocKey / Count 三格）⇒ **下标是唯一定位键**（从 0 起，
顺序就是它在字节里的顺序）。三个动作各自可撤（AGENTS §0.10）：

| 动词 | 本模块 | 条数 |
|---|---|---|
| **新增** | add_trigger | N -> N+1（追加在末尾，既有下标一个都不动） |
| **删除** | remove_trigger | N -> N-1（越界 ⇒ 抛，并把现有那条清单印出来） |
| **修改** | update_trigger | 等长（只动给出来的那几格，这一条上多出来的键原样留着） |

⛔ **产品不造任何默认值**：三格都要调用方给（make_trigger 三格全要），
要「无解锁条件」就是**空表**（CLI 的 `--drop-triggers`），不是拿某个值占位。

## G2：两个实例，本模块**不替你选**

容器里实测有两个（读数见 kb/30 §4）：

| pathID | 字节 | 谁引用它 |
|---|---|---|
| **52174** | **220,220** | GameConfig **52089** ＋ EpauletsConfig 52156 |
| 52209 | 212,504 | GameConfig 52181 ＋ EpauletsConfig 52195 |

ResourceManager 名字表里**唯一**那条 gameconfig 就是 52089（kb/30 §4 有全套读数）。
两者 **m_Name 一模一样**、同一个 MonoScript(10835)，所以「按名字认」这一步
**分不出谁是谁** ⇒ path_id **必须由调用方点名**（判据一行会把读数打出来）；
⛔ 本模块不猜、不回落、不自动挑一个。

## 落点与代价（实测，设计稿 §1）

写的是 **data.unity3d 里那一个对象的字节**，走 core/asset/ufs.py 的选择性写回
rewrite_container：只重切被碰到的块，干净块按压缩字节原样搬。
整文件 6.63 GB 重写，实测约 4 分钟 —— 远高于 ③ 那一档（units 包约 30 秒）。

⚠ 新字节长度变了就**不能原地覆盖**：rewrite_container 会平移后面所有对象的
byte_start、改目标对象表项的 byte_size、重算节点表与 SerializedFile 头
file_size。这三件事由它自己做，本模块只负责「哪个区间换成什么」。

## 守卫（fail-closed，⛔ 一条都不许静默）

* **单位 Id 不在 Skins 表里** => 抛，并把既有单位 Id 印出来（⛔ 不新增单位键）；
* **皮肤 Id 撞（本单位）** => 抛，并把既有 Id 印出来；
* **皮肤 Id 撞（全局，别家单位已用）** => 抛，并**点名是哪个单位**占了它 ——
  ⚠ 2026-10-11 真机现场：只查本单位漏了这条 ⇒ 游戏侧 `GetAllSkins()` 抛
  `ArgumentException: An item with the same key has already been added` ⇒
  `UnitInfoCard.SetUnitSkins` 整个失败 ⇒ **军械库那张卡建不出来**（`Couldn't load unit`）。
  皮肤 Id 是**全局键**（`Skins.Values → SelectMany(Data) → ToDictionary(x => x.Id)`）；
* **参照条不存在 / 不在同一个单位** => 抛（新增要照抄参照条，见下）；
* **条目不存在**（删 / 改点了不存在的 Id）=> 抛，并把既有 Id 印出来；
* **触发器下标越界**（删 / 改点了不存在的第 N 条）=> 抛，并把现有那条清单印出来；
* **Id 不是正数** => 抛（Id 是跨表连接键，0 / 负数不可能是真件里的值）；
* **空改动**（改完之后逐字段没有任何变化）=> 抛。

## 为什么新增必须给参照条（⛔ 不许手搓默认值）

SkinItemData 有 10 个字段，产品只管 6 个（Id / SkinPack / Triggers /
IsDefault / Free / Disabled）；剩下 4 个（Grade / HideUnlockInfo /
NoneElement / HideIfNotUnlocked）是**游戏语义**（档位、解锁提示的显示条件），
我们不知道新条目该填什么。所以新增只有一条路：**给一条参照条（同单位下已有的
条目），这 4 个字段原样照抄**，产品只覆盖那 6 个。
参照条不存在 => **拒绝**（⛔ 不回落、不造默认值）。

## 自证（plan 阶段做完，任何一条不成立就抛）

① 原对象 typetree 往返**逐字节相同**（read_container_config）；
② 新字节能**读回**、读回的表 == 期望；且读回再写一遍是同一串；
③ 新旧对象字节相同 => 抛（空改动）。

## 写后复核（install 里做，不通过就删掉半成品）

1. 从**新文件**读回那个对象，逐条与期望比对；
2. 同一节点里**其它对象逐字节未变**。这条要把整个节点的对象读一遍 ——
   在 data.unity3d 上约 671 MB 解压，是那 4 分钟预算里的一部分；⛔ 不许关掉。
3. 结果落成 InstallResult.lines（判据一行）。

## 反向 Δ（ADR 0007：⛔ 不拷整包当备份）

ReverseDelta 只记**被替换那一段的原始字节**（＋ 哪个节点 / 哪个 pathID /
替换后的长度）。restore 时**按 pathID 重新定位**（不信任记下来的绝对偏移），
把原字节写回去 —— 还原逐位。
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import clone
from ba_mod_maker.asset_kernel import reverse_delta as rdelta
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

#: ⚠ rdelta = reverse_delta 那个**模块**的别名（注释不能夹在 import 块中间，ruff I001）。
#: 本模块自己有一个同名的函数 reverse_delta（把一条干跑结果收成一条反向 Δ），
#: 裸 import 会把函数名盖掉。

__all__ = [
    "CONFIG_NAME",
    "COPIED_FIELDS",
    "DISABLED_FIELD",
    "ID_FIELD",
    "IS_DEFAULT_FIELD",
    "ITEM_DATA_FIELD",
    "KEY_FIELD",
    "NODE_PATH",
    "SKINS_FIELD",
    "SKIN_PACK_FIELD",
    "TRIGGERS_FIELD",
    "TRIGGER_FIELDS",
    "VALUE_FIELD",
    "ConfigObject",
    "InstallResult",
    "ReverseDelta",
    "SkinItem",
    "UserItemsPlan",
    "add_skin_item",
    "add_trigger",
    "install",
    "item_of",
    "item_owner",
    "make_trigger",
    "plan_add",
    "plan_remove",
    "plan_update",
    "read_container_config",
    "read_unit_items",
    "remove_skin_item",
    "remove_trigger",
    "restore",
    "reverse_delta",
    "serialize_config",
    "set_skin_items",
    "skin_items",
    "trigger_at",
    "trigger_names",
    "type_tree",
    "unit_ids",
    "update_skin_item",
    "update_trigger",
    "verify_install",
]

# --------------------------------------------------------------------------- #
# 字段名（typetree 里的原样拼写 —— 一个字符都不许改）
# --------------------------------------------------------------------------- #
#: MonoScript 认的那个类名（也是两个实例的 m_Name，实测**一模一样**）。
CONFIG_NAME = "UserItemsConfig"
#: 配置对象所在的节点（data.unity3d 的节点 [2]，671,372,372 B）。
NODE_PATH = "globalgamemanagers.assets"
#: 单位 Id -> 皮肤组 的那张表。
SKINS_FIELD = "Skins"
#: SkinGroupDict 的平行数组：单位 Id 列表。
KEY_FIELD = "_keyData"
#: 同上：每个单位一个 SkinGroupData。
VALUE_FIELD = "_valueData"
#: SkinGroupData 里那条数组（一条 = 一个皮肤）。
ITEM_DATA_FIELD = "Data"
#: SkinItemData 的字段（产品能改的 6 个）。
ID_FIELD = "Id"
SKIN_PACK_FIELD = "SkinPack"
TRIGGERS_FIELD = "Triggers"
IS_DEFAULT_FIELD = "IsDefault"
FREE_FIELD = "Free"
DISABLED_FIELD = "Disabled"

#: 产品**不管**的 4 个字段名（新增时从参照条原样照抄；判据一行里会点名）。
COPIED_FIELDS: tuple[str, ...] = (
    "Grade",
    "HideUnlockInfo",
    "NoneElement",
    "HideIfNotUnlocked",
)

#: 触发器的字段（UnlockTriggersData）—— 名字以**真件类型树**为准（实测三项）：
#: Trigger / DescriptionLocKey / Count。整条触发器**原样保留**（不翻译、不裁剪），
#: 游戏以后加字段也不会被我们吃掉。
TRIGGER_FIELD = "Trigger"
TRIGGER_LOC_KEY_FIELD = "DescriptionLocKey"
TRIGGER_COUNT_FIELD = "Count"


# --------------------------------------------------------------------------- #
# 冻结的类型树（来源：prefabsme 包类型表 type_id=233，Unity 自己写的树）
# --------------------------------------------------------------------------- #
_TREE_CACHE: tuple[clone.TypeNode, ...] | None = None
#: 一行一个节点：level TAB type TAB name TAB byte_size TAB meta_flag TAB type_flags TAB version
_TREE_TEXT = """
0	MonoBehaviour	Base	-1	32768	0	1
1	PPtr<GameObject>	m_GameObject	12	65	0	1
2	int	m_FileID	4	65	0	1
2	SInt64	m_PathID	8	65	0	1
1	UInt8	m_Enabled	1	16641	0	1
1	PPtr<MonoScript>	m_Script	12	0	0	1
2	int	m_FileID	4	8388609	0	1
2	SInt64	m_PathID	8	8388609	0	1
1	string	m_Name	-1	557057	0	1
2	Array	Array	-1	540673	1	1
3	int	size	4	524289	0	1
3	char	data	1	524289	0	1
1	vector	_errors	-1	32768	0	1
2	Array	Array	-1	49152	1	1
3	int	size	4	0	0	1
3	string	data	-1	32768	0	1
4	Array	Array	-1	16385	1	1
5	int	size	4	1	0	1
5	char	data	1	1	0	1
1	UserItemData	Avatars	-1	32768	0	1
2	Array	Array	-1	32768	1	1
3	int	size	4	0	0	1
3	UserItemData	data	-1	32768	0	1
4	int	Id	4	0	0	1
4	PPtr<$Sprite>	ItemImage	12	0	0	1
5	int	m_FileID	4	8388609	0	1
5	SInt64	m_PathID	8	8388609	0	1
4	string	ItemVideoLink	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	UInt8	Grade	1	16384	0	1
4	string	TechnicalName	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	string	TitleLocKey	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	string	DescriptionLocKey	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	int	UiOrder	4	0	0	1
4	UnlockTriggersData	Triggers	-1	32768	0	1
5	Array	Array	-1	32768	1	1
6	int	size	4	0	0	1
6	UnlockTriggersData	data	-1	32768	0	1
7	string	Trigger	-1	32768	0	1
8	Array	Array	-1	16385	1	1
9	int	size	4	1	0	1
9	char	data	1	1	0	1
7	string	DescriptionLocKey	-1	32768	0	1
8	Array	Array	-1	16385	1	1
9	int	size	4	1	0	1
9	char	data	1	1	0	1
7	unsigned int	Count	4	0	0	1
4	UInt8	HideUnlockInfo	1	16640	0	1
4	UInt8	IsDefault	1	16640	0	1
4	UInt8	NoneElement	1	16640	0	1
4	UInt8	HideIfNotUnlocked	1	16640	0	1
4	UInt8	Disabled	1	16640	0	1
4	UInt8	Free	1	16640	0	1
4	int	ContentMembership	4	0	0	1
1	UserItemData	Patches	-1	32768	0	1
2	Array	Array	-1	32768	1	1
3	int	size	4	0	0	1
3	UserItemData	data	-1	32768	0	1
4	int	Id	4	0	0	1
4	PPtr<$Sprite>	ItemImage	12	0	0	1
5	int	m_FileID	4	8388609	0	1
5	SInt64	m_PathID	8	8388609	0	1
4	string	ItemVideoLink	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	UInt8	Grade	1	16384	0	1
4	string	TechnicalName	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	string	TitleLocKey	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	string	DescriptionLocKey	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	int	UiOrder	4	0	0	1
4	UnlockTriggersData	Triggers	-1	32768	0	1
5	Array	Array	-1	32768	1	1
6	int	size	4	0	0	1
6	UnlockTriggersData	data	-1	32768	0	1
7	string	Trigger	-1	32768	0	1
8	Array	Array	-1	16385	1	1
9	int	size	4	1	0	1
9	char	data	1	1	0	1
7	string	DescriptionLocKey	-1	32768	0	1
8	Array	Array	-1	16385	1	1
9	int	size	4	1	0	1
9	char	data	1	1	0	1
7	unsigned int	Count	4	0	0	1
4	UInt8	HideUnlockInfo	1	16640	0	1
4	UInt8	IsDefault	1	16640	0	1
4	UInt8	NoneElement	1	16640	0	1
4	UInt8	HideIfNotUnlocked	1	16640	0	1
4	UInt8	Disabled	1	16640	0	1
4	UInt8	Free	1	16640	0	1
4	int	ContentMembership	4	0	0	1
1	UserItemData	Frames	-1	32768	0	1
2	Array	Array	-1	32768	1	1
3	int	size	4	0	0	1
3	UserItemData	data	-1	32768	0	1
4	int	Id	4	0	0	1
4	PPtr<$Sprite>	ItemImage	12	0	0	1
5	int	m_FileID	4	8388609	0	1
5	SInt64	m_PathID	8	8388609	0	1
4	string	ItemVideoLink	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	UInt8	Grade	1	16384	0	1
4	string	TechnicalName	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	string	TitleLocKey	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	string	DescriptionLocKey	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	int	UiOrder	4	0	0	1
4	UnlockTriggersData	Triggers	-1	32768	0	1
5	Array	Array	-1	32768	1	1
6	int	size	4	0	0	1
6	UnlockTriggersData	data	-1	32768	0	1
7	string	Trigger	-1	32768	0	1
8	Array	Array	-1	16385	1	1
9	int	size	4	1	0	1
9	char	data	1	1	0	1
7	string	DescriptionLocKey	-1	32768	0	1
8	Array	Array	-1	16385	1	1
9	int	size	4	1	0	1
9	char	data	1	1	0	1
7	unsigned int	Count	4	0	0	1
4	UInt8	HideUnlockInfo	1	16640	0	1
4	UInt8	IsDefault	1	16640	0	1
4	UInt8	NoneElement	1	16640	0	1
4	UInt8	HideIfNotUnlocked	1	16640	0	1
4	UInt8	Disabled	1	16640	0	1
4	UInt8	Free	1	16640	0	1
4	int	ContentMembership	4	0	0	1
1	UserItemData	Icons	-1	32768	0	1
2	Array	Array	-1	32768	1	1
3	int	size	4	0	0	1
3	UserItemData	data	-1	32768	0	1
4	int	Id	4	0	0	1
4	PPtr<$Sprite>	ItemImage	12	0	0	1
5	int	m_FileID	4	8388609	0	1
5	SInt64	m_PathID	8	8388609	0	1
4	string	ItemVideoLink	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	UInt8	Grade	1	16384	0	1
4	string	TechnicalName	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	string	TitleLocKey	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	string	DescriptionLocKey	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	int	UiOrder	4	0	0	1
4	UnlockTriggersData	Triggers	-1	32768	0	1
5	Array	Array	-1	32768	1	1
6	int	size	4	0	0	1
6	UnlockTriggersData	data	-1	32768	0	1
7	string	Trigger	-1	32768	0	1
8	Array	Array	-1	16385	1	1
9	int	size	4	1	0	1
9	char	data	1	1	0	1
7	string	DescriptionLocKey	-1	32768	0	1
8	Array	Array	-1	16385	1	1
9	int	size	4	1	0	1
9	char	data	1	1	0	1
7	unsigned int	Count	4	0	0	1
4	UInt8	HideUnlockInfo	1	16640	0	1
4	UInt8	IsDefault	1	16640	0	1
4	UInt8	NoneElement	1	16640	0	1
4	UInt8	HideIfNotUnlocked	1	16640	0	1
4	UInt8	Disabled	1	16640	0	1
4	UInt8	Free	1	16640	0	1
4	int	ContentMembership	4	0	0	1
1	UserItemData	Epaulets	-1	32768	0	1
2	Array	Array	-1	32768	1	1
3	int	size	4	0	0	1
3	UserItemData	data	-1	32768	0	1
4	int	Id	4	0	0	1
4	PPtr<$Sprite>	ItemImage	12	0	0	1
5	int	m_FileID	4	8388609	0	1
5	SInt64	m_PathID	8	8388609	0	1
4	string	ItemVideoLink	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	UInt8	Grade	1	16384	0	1
4	string	TechnicalName	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	string	TitleLocKey	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	string	DescriptionLocKey	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	int	UiOrder	4	0	0	1
4	UnlockTriggersData	Triggers	-1	32768	0	1
5	Array	Array	-1	32768	1	1
6	int	size	4	0	0	1
6	UnlockTriggersData	data	-1	32768	0	1
7	string	Trigger	-1	32768	0	1
8	Array	Array	-1	16385	1	1
9	int	size	4	1	0	1
9	char	data	1	1	0	1
7	string	DescriptionLocKey	-1	32768	0	1
8	Array	Array	-1	16385	1	1
9	int	size	4	1	0	1
9	char	data	1	1	0	1
7	unsigned int	Count	4	0	0	1
4	UInt8	HideUnlockInfo	1	16640	0	1
4	UInt8	IsDefault	1	16640	0	1
4	UInt8	NoneElement	1	16640	0	1
4	UInt8	HideIfNotUnlocked	1	16640	0	1
4	UInt8	Disabled	1	16640	0	1
4	UInt8	Free	1	16640	0	1
4	int	ContentMembership	4	0	0	1
1	SkinGroupDict	Skins	-1	32768	0	1
2	vector	_keyData	-1	32769	0	1
3	Array	Array	-1	16385	1	1
4	int	size	4	1	0	1
4	int	data	4	1	0	1
2	SkinGroupData	_valueData	-1	32769	0	1
3	Array	Array	-1	32769	1	1
4	int	size	4	1	0	1
4	SkinGroupData	data	-1	32769	0	1
5	SkinItemData	Data	-1	32769	0	1
6	Array	Array	-1	32769	1	1
7	int	size	4	1	0	1
7	SkinItemData	data	-1	32769	0	1
8	int	Id	4	1	0	1
8	UInt8	Grade	1	16385	0	1
8	string	SkinPack	-1	32769	0	1
9	Array	Array	-1	16385	1	1
10	int	size	4	1	0	1
10	char	data	1	1	0	1
8	UnlockTriggersData	Triggers	-1	32769	0	1
9	Array	Array	-1	32769	1	1
10	int	size	4	1	0	1
10	UnlockTriggersData	data	-1	32769	0	1
11	string	Trigger	-1	32769	0	1
12	Array	Array	-1	16385	1	1
13	int	size	4	1	0	1
13	char	data	1	1	0	1
11	string	DescriptionLocKey	-1	32769	0	1
12	Array	Array	-1	16385	1	1
13	int	size	4	1	0	1
13	char	data	1	1	0	1
11	unsigned int	Count	4	1	0	1
8	UInt8	HideUnlockInfo	1	16641	0	1
8	UInt8	IsDefault	1	16641	0	1
8	UInt8	NoneElement	1	16641	0	1
8	UInt8	HideIfNotUnlocked	1	16641	0	1
8	UInt8	Disabled	1	16641	0	1
8	UInt8	Free	1	16641	0	1
1	SkinPackDict	SkinPacks	-1	32768	0	1
2	vector	_keyData	-1	32769	0	1
3	Array	Array	-1	49153	1	1
4	int	size	4	1	0	1
4	string	data	-1	32769	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
2	SkinPackData	_valueData	-1	32769	0	1
3	Array	Array	-1	32769	1	1
4	int	size	4	1	0	1
4	SkinPackData	data	-1	32769	0	1
5	PPtr<$Sprite>	Preview	12	1	0	1
6	int	m_FileID	4	8388609	0	1
6	SInt64	m_PathID	8	8388609	0	1
5	string	TechnicalName	-1	32769	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	char	data	1	1	0	1
5	string	TitleLocKey	-1	32769	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	char	data	1	1	0	1
5	string	DescriptionLocKey	-1	32769	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	char	data	1	1	0	1
5	int	UiOrder	4	1	0	1
5	int	ContentMembership	4	1	0	1
1	DLCMarketDict	DLC	-1	32768	0	1
2	vector	_keyData	-1	49153	0	1
3	Array	Array	-1	16385	1	1
4	int	size	4	1	0	1
4	UInt8	data	1	1	0	1
2	DCLMarketDataWrapper	_valueData	-1	32769	0	1
3	Array	Array	-1	32769	1	1
4	int	size	4	1	0	1
4	DCLMarketDataWrapper	data	-1	32769	0	1
5	DLCMarketData	Data	-1	32769	0	1
6	Array	Array	-1	32769	1	1
7	int	size	4	1	0	1
7	DLCMarketData	data	-1	32769	0	1
8	int	Id	4	1	0	1
8	string	Name	-1	32769	0	1
9	Array	Array	-1	16385	1	1
10	int	size	4	1	0	1
10	char	data	1	1	0	1
1	DLCContainer	DLCContainers	-1	32768	0	1
2	Array	Array	-1	32768	1	1
3	int	size	4	0	0	1
3	DLCContainer	data	-1	32768	0	1
4	int	Id	4	0	0	1
4	string	Name	-1	32768	0	1
5	Array	Array	-1	16385	1	1
6	int	size	4	1	0	1
6	char	data	1	1	0	1
4	DLCContainerDict	Data	-1	32768	0	1
5	vector	_keyData	-1	49153	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	UInt8	data	1	1	0	1
5	DLCContainerMarketData	_valueData	-1	32769	0	1
6	Array	Array	-1	32769	1	1
7	int	size	4	1	0	1
7	DLCContainerMarketData	data	-1	32769	0	1
8	vector	Data	-1	32769	0	1
9	Array	Array	-1	16385	1	1
10	int	size	4	1	0	1
10	int	data	4	1	0	1
8	string	TitleLocKey	-1	32769	0	1
9	Array	Array	-1	16385	1	1
10	int	size	4	1	0	1
10	char	data	1	1	0	1
8	string	MarketUrl	-1	32769	0	1
9	Array	Array	-1	16385	1	1
10	int	size	4	1	0	1
10	char	data	1	1	0	1
4	AssetReferenceTexture2D	HomeImage	-1	32768	0	1
5	string	m_AssetGUID	-1	32768	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	char	data	1	1	0	1
5	string	m_SubObjectName	-1	32768	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	char	data	1	1	0	1
5	string	m_SubObjectType	-1	32768	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	char	data	1	1	0	1
4	AssetReferenceTexture2D	VerticalImage	-1	32768	0	1
5	string	m_AssetGUID	-1	32768	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	char	data	1	1	0	1
5	string	m_SubObjectName	-1	32768	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	char	data	1	1	0	1
5	string	m_SubObjectType	-1	32768	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	char	data	1	1	0	1
4	AssetReferenceTexture2D	HorizontalImage	-1	32768	0	1
5	string	m_AssetGUID	-1	32768	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	char	data	1	1	0	1
5	string	m_SubObjectName	-1	32768	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	char	data	1	1	0	1
5	string	m_SubObjectType	-1	32768	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	char	data	1	1	0	1
4	AssetReferenceTexture2D	Icon	-1	32768	0	1
5	string	m_AssetGUID	-1	32768	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	char	data	1	1	0	1
5	string	m_SubObjectName	-1	32768	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	char	data	1	1	0	1
5	string	m_SubObjectType	-1	32768	0	1
6	Array	Array	-1	16385	1	1
7	int	size	4	1	0	1
7	char	data	1	1	0	1
4	int	ContentMembership	4	0	0	1
"""


def type_tree() -> tuple[clone.TypeNode, ...]:
    """本模块用的 SerializedFile 字段树（**冻结**，从真件抄来，不猜）。

    data.unity3d 的 91 个节点**全部** enable_type_tree=False（实测），所以
    globalgamemanagers.assets 里没有可用的内嵌树；这棵树来自 prefabsme 包里
    同一脚本的那一项（Unity 编译时写进去的），对 data.unity3d 里的对象**逐字节可复现**。

    判据一行（正控，实测三个真件对象全部 True；复算 _work/uservis/probe_h2_tree.py）：

        prefabsme pid=-859803293313809984 (220,220 B) 往返逐字节相同 = True
        data pid=52174 (220,220 B) 往返逐字节相同 = True
        data pid=52209 (212,504 B) 往返逐字节相同 = True

    往返不可复现 ⇒ 这棵树与真件不符，本模块一律拒绝写（见 read_container_config）。
    """
    global _TREE_CACHE
    if _TREE_CACHE is None:
        nodes: list[clone.TypeNode] = []
        for line in _TREE_TEXT.strip().splitlines():
            if not line.strip():
                continue
            level, typ, name, size, meta, flags, ver = line.split("\t")
            nodes.append(
                clone.TypeNode(
                    level=int(level),
                    type=typ,
                    name=name,
                    byte_size=int(size),
                    meta_flag=int(meta),
                    type_flags=int(flags),
                    version=int(ver),
                )
            )
        if not nodes:
            raise AssetError(
                "冻结的类型树是空的",
                hint="user_items._TREE_TEXT 被清掉了；重跑 _work/uservis/probe_h2_tree.py 重新冻结",
            )
        _TREE_CACHE = tuple(nodes)
    return _TREE_CACHE


# --------------------------------------------------------------------------- #
# 守卫小件
# --------------------------------------------------------------------------- #
def _mapping_of(value: Any, *, where: str, what: str) -> Mapping[str, Any]:
    """取一个映射；形状不对**抛**。

    与 prefab_skins 的小件同名不同源是**刻意**的：两张表的失败提示不一样，
    共用一份会让人以为是另一层坏了。
    """
    if not isinstance(value, Mapping):
        raise AssetError(
            f"{where} 不是映射（读到 {type(value).__name__}）",
            hint=f"{what} 的字段树形状与真件不符 —— 先看清楚再写，⛔ 不许盲改",
        )
    return value


def _sequence_of(value: Any, *, where: str, what: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AssetError(
            f"{where} 不是序列（读到 {type(value).__name__}）",
            hint=f"{what} 是一条数组（vector）—— 形状不对说明读的不是这张表",
        )
    return value


def _int_at(row: Mapping[str, Any], field: str, *, where: str) -> int:
    value = row.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise AssetError(
            f"{where}.{field} 不是整数（读到 {type(value).__name__}）",
            hint="真件类型树里这几个字段都是 int32 —— 类型不对说明读的不是这条表",
        )
    return int(value)


def _str_at(row: Mapping[str, Any], field: str, *, where: str) -> str:
    value = row.get(field)
    if not isinstance(value, str):
        raise AssetError(
            f"{where}.{field} 不是字符串（读到 {type(value).__name__}）",
            hint="真件类型树里 SkinPack 是 string —— 类型不对说明读的不是这条表",
        )
    return value


def _check_positive(item_id: int, *, where: str) -> int:
    if int(item_id) <= 0:
        raise AssetError(
            f"{where}：皮肤 Id={int(item_id)} 不是正数",
            hint="Id 是跨表连接键（③ 的 UnitPrefabRoot.Skins.Data 与本表同号才看得见）；"
            "0 / 负数不可能是真件里的值",
        )
    return int(item_id)


# --------------------------------------------------------------------------- #
# 纯函数层：一条皮肤的数据形状
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class SkinItem:
    """一条 SkinItemData 里**产品管得着**的那 6 个字段。

    Triggers 记成**原样的映射元组**（整条 UnlockTriggersData 照抄）—— 保留顺序与重复，
    逐字段比较时不会因为 dict 的写法不同而误判成「变了」；游戏侧以后加字段也不会被吃掉。
    ⚠ 装了 dict 的冻结数据类**不可哈希**（拿它当 dict 的键会炸）—— 本模块一律按值比较。
    """

    item_id: int
    skin_pack: str
    triggers: tuple[Mapping[str, Any], ...]
    is_default: int
    free: int
    disabled: int

    def summary(self) -> str:
        # 触发器印**三格 ＋ 下标**（trigger_names）：下标是它的唯一定位键，
        # 干跑/--json 印出来的就是挑下标要用的那张表。
        return (
            f"Id={self.item_id} 皮肤包={self.skin_pack!r} 触发器={trigger_names(self)} "
            f"IsDefault={self.is_default} Free={self.free} Disabled={self.disabled}"
        )


_SKIN_ITEM_FIELDS = ("item_id", "skin_pack", "triggers", "is_default", "free", "disabled")


def _triggers_of(row: Mapping[str, Any], *, where: str) -> tuple[Mapping[str, Any], ...]:
    """整条触发器**原样深拷**（只做形状检查，不翻译、不裁剪）。

    形状检查只针对真件类型树里那三项；多出来的键照样留着。
    """
    raw = _sequence_of(
        row.get(TRIGGERS_FIELD, ()),
        where=f"{where}.{TRIGGERS_FIELD}",
        what="UnlockTriggersData[]",
    )
    out: list[Mapping[str, Any]] = []
    for index, item in enumerate(raw):
        twhere = f"{where}.{TRIGGERS_FIELD}[{index}]"
        entry = _mapping_of(item, where=twhere, what="UnlockTriggersData")
        for field in (TRIGGER_FIELD, TRIGGER_LOC_KEY_FIELD):
            if field in entry:
                _str_at(entry, field, where=twhere)
        if TRIGGER_COUNT_FIELD in entry and not isinstance(entry[TRIGGER_COUNT_FIELD], int):
            raise AssetError(
                f"{twhere}.{TRIGGER_COUNT_FIELD} 不是整数"
                f"（读到 {type(entry[TRIGGER_COUNT_FIELD]).__name__}）",
                hint="真件类型树里它是 unsigned int —— 类型不对说明读的不是这条表",
            )
        out.append(copy.deepcopy(dict(entry)))
    return tuple(out)


def _unit_slot(tree: Mapping[str, Any], unit_id: int) -> int:
    """单位 Id 在 _keyData 里的下标；不在就抛（并把既有单位列出来）。"""
    skins = _mapping_of(tree.get(SKINS_FIELD), where=SKINS_FIELD, what="SkinGroupDict")
    keys = _sequence_of(skins.get(KEY_FIELD), where=f"{SKINS_FIELD}.{KEY_FIELD}", what="int[]")
    values = _sequence_of(
        skins.get(VALUE_FIELD), where=f"{SKINS_FIELD}.{VALUE_FIELD}", what="SkinGroupData[]"
    )
    if len(keys) != len(values):
        raise AssetError(
            f"{SKINS_FIELD} 的平行数组长度不等：_keyData {len(keys)} / _valueData {len(values)}",
            hint="_keyData 与 _valueData 是**配对**的（UnitySerializedDictionary）——"
            "只改一边就是写坏这张表，⛔ 不许按「看起来对」补",
        )
    hits = [i for i, key in enumerate(keys) if int(key) == int(unit_id)]
    if not hits:
        raise AssetError(
            f"{SKINS_FIELD} 里没有单位 Id={int(unit_id)} 这个键"
            f"（既有 {len(keys)} 个单位：{list(keys)[:12]}…）",
            hint="单位 Id 就是数据库 Units.Id（设计稿 §1.1：_keyData[210]=9 是 T-72B）。"
            "⛔ 本模块不新增单位键 —— 只改既有单位下的皮肤条目",
        )
    if len(hits) > 1:
        raise AssetError(
            f"{SKINS_FIELD} 里单位 Id={int(unit_id)} 出现了 {len(hits)} 次（下标 {hits}）",
            hint="这张表里一个单位只该有一条 —— 出现重复说明读法或数据有问题，别动它",
        )
    return hits[0]


def _raw_rows(tree: Mapping[str, Any], unit_id: int) -> tuple[Mapping[str, Any], ...]:
    """某个单位的**原始行**（不翻译；形状不对 / 单位键不在就抛）。"""
    slot = _unit_slot(tree, unit_id)
    skins = _mapping_of(tree.get(SKINS_FIELD), where=SKINS_FIELD, what="SkinGroupDict")
    values = _sequence_of(
        skins.get(VALUE_FIELD), where=f"{SKINS_FIELD}.{VALUE_FIELD}", what="SkinGroupData[]"
    )
    where = f"{SKINS_FIELD}.{VALUE_FIELD}[{slot}]"
    group = _mapping_of(values[slot], where=where, what="SkinGroupData")
    return tuple(
        _sequence_of(
            group.get(ITEM_DATA_FIELD, ()),
            where=f"{where}.{ITEM_DATA_FIELD}",
            what="SkinItemData[]",
        )
    )


def unit_ids(tree: Mapping[str, Any]) -> tuple[int, ...]:
    """**纯函数**：Skins 表里有皮肤组的那些单位 Id（原样顺序）。"""
    skins = _mapping_of(tree.get(SKINS_FIELD), where=SKINS_FIELD, what="SkinGroupDict")
    return tuple(
        int(k)
        for k in _sequence_of(
            skins.get(KEY_FIELD), where=f"{SKINS_FIELD}.{KEY_FIELD}", what="int[]"
        )
    )


def skin_items(tree: Mapping[str, Any], unit_id: int) -> tuple[SkinItem, ...]:
    """**纯函数**：读某个单位的皮肤条目。

    形状不对（字段不在 / 不是序列 / 单位键不在 / 平行数组不等）一律抛 AssetError，
    ⛔ **不静默当空** —— 把「读法坏了」写成「这个单位没有皮肤」，
    下一步就是拿一张假空表去写盘。
    """
    out: list[SkinItem] = []
    for index, row in enumerate(_raw_rows(tree, unit_id)):
        where = f"{SKINS_FIELD}[{int(unit_id)}].{ITEM_DATA_FIELD}[{index}]"
        entry = _mapping_of(row, where=where, what="SkinItemData")
        out.append(
            SkinItem(
                item_id=_check_positive(_int_at(entry, ID_FIELD, where=where), where=where),
                skin_pack=_str_at(entry, SKIN_PACK_FIELD, where=where),
                triggers=_triggers_of(entry, where=where),
                is_default=_int_at(entry, IS_DEFAULT_FIELD, where=where),
                free=_int_at(entry, FREE_FIELD, where=where),
                disabled=_int_at(entry, DISABLED_FIELD, where=where),
            )
        )
    return tuple(out)


def item_of(tree: Mapping[str, Any], unit_id: int, item_id: int) -> SkinItem:
    """按 Id 取一条；不存在 ⇒ 抛（并把既有 Id 列出来）。"""
    items = skin_items(tree, unit_id)
    for item in items:
        if item.item_id == int(item_id):
            return item
    ids = [i.item_id for i in items]
    raise AssetError(
        f"单位 {int(unit_id)} 的皮肤表里没有 Id={int(item_id)} 这一条"
        f"（既有 {len(items)} 条：{ids}）",
        hint="删除 / 修改的对象必须是**这张表里真有**的那一条 —— ⛔ 不许按行下标猜",
    )


def _item_row(item: SkinItem, reference: Mapping[str, Any] | None) -> dict[str, Any]:
    """按真件字段树拼回一行；reference 给了就**照抄**它那 4 个产品不管的字段。"""
    row: dict[str, Any] = {}
    if reference is not None:
        for field in COPIED_FIELDS:
            row[field] = copy.deepcopy(reference.get(field))
    row[ID_FIELD] = int(item.item_id)
    row[SKIN_PACK_FIELD] = str(item.skin_pack)
    row[TRIGGERS_FIELD] = [copy.deepcopy(dict(t)) for t in item.triggers]
    row[IS_DEFAULT_FIELD] = int(item.is_default)
    row[FREE_FIELD] = int(item.free)
    row[DISABLED_FIELD] = int(item.disabled)
    return row


def set_skin_items(
    tree: Mapping[str, Any],
    unit_id: int,
    items: Sequence[SkinItem],
    *,
    new_reference_id: int | None = None,
) -> dict[str, Any]:
    """**纯函数**：把某个单位的皮肤条目换成 items（入参 tree **不被改**）。

    没动过的行**原样照抄**（深拷旧行）；**值变了的既有条目从它自己那一行重拼**
    （那 4 个产品不管的字段照抄它**自己**的那一行）；只有**新 Id** 才拿 new_reference_id
    那一条（不给就用该单位第一条）当私有字段来源。这样「加一条」的字节差就是那一条，
    不会顺手把别的行重排，也不会把改的那一行的 Grade 洗成第 0 行的。

    ⚠ 新条的 Id 在 items 里必须**已经**是正的；撞号由 add_skin_item 查，
    这里只管形状。
    """
    raw = list(_raw_rows(tree, unit_id))
    original = skin_items(tree, unit_id)
    pairs = tuple(zip(original, raw, strict=True))
    reference: Mapping[str, Any] | None = raw[0] if raw else None
    if new_reference_id is not None:
        target = int(new_reference_id)
        reference = next((row for item, row in pairs if item.item_id == target), None)
        if reference is None:
            raise AssetError(
                f"参照条 Id={target} 不在单位 {int(unit_id)} 的皮肤表里"
                f"（既有 {[i.item_id for i in original]}）",
                hint="新增的参照条必须是**同一个单位**下既有的一条 —— ⛔ 不许跨单位照抄、不许手搓",
            )
    by_id = {cand.item_id: row for cand, row in pairs}
    rows: list[dict[str, Any]] = []
    for item in items:
        _check_positive(item.item_id, where="set_skin_items")
        # ⚠ 按**值**找旧行，不用 dict 当键：SkinItem 里装着 tuple[Mapping]，不可哈希。
        same = next((row for cand, row in pairs if cand == item), None)
        if same is not None:
            rows.append(copy.deepcopy(dict(same)))  # 逐字段没变过的行：原样照抄
            continue
        # 值变了的**既有条目**：私有字段的来源是**它自己**那一行。
        # ⛔ 不是第 0 行 —— 拿第 0 行当来源会静默洗掉这一行的
        # Grade / HideUnlockInfo / NoneElement / HideIfNotUnlocked
        # （实测：两行 Grade [3, 7]，改第二行的 Disabled 之后变 [3, 3]）。
        rows.append(_item_row(item, by_id.get(item.item_id, reference)))
    return _replace_rows(tree, unit_id, rows)


def _replace_rows(
    tree: Mapping[str, Any], unit_id: int, rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    slot = _unit_slot(tree, unit_id)
    skins = dict(_mapping_of(tree.get(SKINS_FIELD), where=SKINS_FIELD, what="SkinGroupDict"))
    keys = list(
        _sequence_of(skins.get(KEY_FIELD), where=f"{SKINS_FIELD}.{KEY_FIELD}", what="int[]")
    )
    values = [
        dict(v) if isinstance(v, Mapping) else v
        for v in _sequence_of(
            skins.get(VALUE_FIELD),
            where=f"{SKINS_FIELD}.{VALUE_FIELD}",
            what="SkinGroupData[]",
        )
    ]
    group = dict(_mapping_of(values[slot], where=f"{SKINS_FIELD}.{VALUE_FIELD}[{slot}]",
                             what="SkinGroupData"))
    group[ITEM_DATA_FIELD] = list(rows)
    values[slot] = group
    skins[KEY_FIELD] = keys
    skins[VALUE_FIELD] = values
    new_tree = dict(tree)
    new_tree[SKINS_FIELD] = skins
    return new_tree


# --------------------------------------------------------------------------- #
# 纯函数层：三个动词
# --------------------------------------------------------------------------- #
def item_owner(tree: Mapping[str, Any], item_id: int) -> int | None:
    """**纯函数**：这个皮肤 Id 在**全局**归哪个单位（没人用就 None）。

    ⚠ **皮肤 Id 是全局键**（2026-10-11 真机现场学会的）：游戏侧
    `UserItemsConfig.GetAllSkins()` 把**所有单位**的皮肤摊平成一个
    `Dictionary<int, SkinItemData>`（`Skins.Values → SelectMany(Data) → ToDictionary`），
    两个单位用同一个 Id ⇒ `ArgumentException: An item with the same key has already been added`
    ⇒ `UnitInfoCard.SetUnitSkins` 整个抛出去 ⇒ **军械库那张卡根本建不出来**
    （日志里表现为 `Error: Couldn't load unit: <单位名>`，卡片整块消失）。
    ⇒ 新增之前**必须扫全局**，⛔ 不能只看目标单位那一张表。
    """
    want = int(item_id)
    for unit in unit_ids(tree):
        for item in skin_items(tree, unit):
            if int(item.item_id) == want:
                return int(unit)
    return None


def add_skin_item(
    tree: Mapping[str, Any], *, unit_id: int, item: SkinItem, reference_id: int
) -> dict[str, Any]:
    """**纯函数**：新增一条（照抄参照条的那 4 个产品不管的字段）—— 条目数 N -> N+1。

    reference_id 必须是**同一个单位**下既有的一条。
    """
    _check_positive(item.item_id, where="add_skin_item")
    items = skin_items(tree, unit_id)
    existing = [i.item_id for i in items]
    if item.item_id in existing:
        raise AssetError(
            f"单位 {int(unit_id)} 的皮肤 Id={item.item_id} 已经在这张表里了"
            f"（既有 {len(existing)} 条：{existing}）",
            hint="Id 是跨表连接键，撞号 ⇒ 两条皮肤在游戏里是同一件东西；"
            "要新加就先想一个新号（真件是连续编号）",
        )
    owner = item_owner(tree, item.item_id)
    if owner is not None and owner != int(unit_id):
        raise AssetError(
            f"皮肤 Id={item.item_id} 在全局已经被**单位 {owner}** 用掉了"
            f"（⛔ 不是本单位的问题：皮肤 Id 是全局键）",
            hint="游戏侧 GetAllSkins() 把所有单位的皮肤摊平成一个字典，撞号 ⇒ 抛 "
            "ArgumentException ⇒ 军械库那张卡**整个建不出来**（真机现场：卡片消失 + "
            "`Couldn't load unit`）。先用 item_owner() 扫一遍全局，挑一个没被用过的号。",
        )
    if int(reference_id) == int(item.item_id):
        raise AssetError(
            f"参照条 Id={int(reference_id)} 与新增条的 Id={item.item_id} 相同",
            hint="参照条是**另一条**既有皮肤 —— 拿同号当参照等于没写新条",
        )
    item_of(tree, unit_id, reference_id)
    after = set_skin_items(
        tree, unit_id, (*items, replace(item)), new_reference_id=int(reference_id)
    )
    got = skin_items(after, unit_id)
    if len(got) != len(items) + 1 or got[-1] != item:
        raise AssetError(
            "新增之后从同一棵树上读回来不是期望的那一条",
            hint=f"写进去 {item.summary()}；读回 {got[-1].summary() if got else '（空）'}",
        )
    return after


def remove_skin_item(tree: Mapping[str, Any], *, unit_id: int, item_id: int) -> dict[str, Any]:
    """**纯函数**：删除一条 —— 条目数 N -> N-1（Id 不存在 ⇒ 抛）。"""
    items = skin_items(tree, unit_id)
    target = int(item_id)
    hits = [index for index, item in enumerate(items) if item.item_id == target]
    if not hits:
        ids = [i.item_id for i in items]
        raise AssetError(
            f"单位 {int(unit_id)} 的皮肤表里没有 Id={target} 这一条"
            f"（既有 {len(items)} 条：{ids}）",
            hint="删除的对象必须是**这张表里真有**的那一条 —— ⛔ 不许按行下标猜",
        )
    index = hits[0]
    return set_skin_items(tree, unit_id, (*items[:index], *items[index + 1 :]))


def update_skin_item(
    tree: Mapping[str, Any], *, unit_id: int, item_id: int, **changes: Any
) -> dict[str, Any]:
    """**纯函数**：修改一条的**既有字段值**（等长档：条目数与结构都不动）。

    changes 的键只认 SkinItem 的字段名（item_id / skin_pack / triggers /
    is_default / free / disabled）；一个都没给、或改完没变化 ⇒ 抛（空改动不该写盘）。
    """
    if not changes:
        raise AssetError(
            f"修改 Id={int(item_id)} 却一个字段都没给 ⇒ 空改动",
            hint="修改 = 改一个**既有字段的值**（AGENTS §0.10）；"
            "什么也不改的动作不该生成写回计划",
        )
    unknown = sorted(set(changes) - set(_SKIN_ITEM_FIELDS))
    if unknown:
        raise AssetError(
            f"修改 Id={int(item_id)} 给了不认识的字段：{unknown}",
            hint=f"只认 {list(_SKIN_ITEM_FIELDS)}；其余 4 个字段（" + "、".join(COPIED_FIELDS)
            + "）是照抄参照条的，本模块不改",
        )
    items = skin_items(tree, unit_id)
    current = item_of(tree, unit_id, item_id)
    changed = replace(current, **changes)
    if changed == current:
        raise AssetError(
            f"修改 Id={int(item_id)} 之后逐字段**没有任何变化** ⇒ 空改动",
            hint=f"现在就是 {current.summary()}；新旧相同不该写盘",
        )
    index = next(i for i, item in enumerate(items) if item.item_id == int(item_id))
    return set_skin_items(tree, unit_id, (*items[:index], changed, *items[index + 1 :]))


# --------------------------------------------------------------------------- #
# 纯函数层：一条触发器（UnlockTriggersData）的新增 / 删除 / 修改
# --------------------------------------------------------------------------- #
#: 一条触发器只有这三格（真件类型树 UnlockTriggersData 实测：Trigger /
#: DescriptionLocKey / Count）。⛔ 产品**不造**任何默认值 —— 三格都要调用方给。
TRIGGER_FIELDS: tuple[str, ...] = (
    TRIGGER_FIELD,
    TRIGGER_LOC_KEY_FIELD,
    TRIGGER_COUNT_FIELD,
)


def trigger_names(item: SkinItem) -> str:
    """**纯函数**：把这条皮肤的触发器印成一行（给人看，也是报错时的既有项清单）。"""
    if not item.triggers:
        return "（无）"
    return "、".join(
        f"[{index}] {row.get(TRIGGER_FIELD, '?')!r}"
        f"（{row.get(TRIGGER_LOC_KEY_FIELD, '?')!r} × {row.get(TRIGGER_COUNT_FIELD, '?')}）"
        for index, row in enumerate(item.triggers)
    )


def trigger_at(item: SkinItem, index: int) -> Mapping[str, Any]:
    """**纯函数**：按下标取第 index 条触发器（越界 ⇒ 抛，并把现有的列出来）。

    下标是**唯一定位键**：UnlockTriggersData 没有 Id（真件类型树里就三格），
    列表顺序就是它在对象字节里的顺序 ⇒ 下标是机械可复算的那个数。
    """
    position = int(index)
    if position < 0 or position >= len(item.triggers):
        raise AssetError(
            f"Id={item.item_id} 的触发器没有下标 {position}"
            f"（现在 {len(item.triggers)} 条：{trigger_names(item)}）",
            hint="触发器没有 Id —— 唯一定位键是**下标**（从 0 起，顺序就是它在字节里的顺序）；"
            "先干跑印一遍那张表再挑",
        )
    return item.triggers[position]


def _trigger_value(field: str, value: Any, *, where: str) -> Any:
    """校验一格的取值（真件类型树：Trigger / DescriptionLocKey = string，Count = unsigned int）。"""
    if field == TRIGGER_COUNT_FIELD:
        if not isinstance(value, int) or isinstance(value, bool):
            raise AssetError(
                f"{where}.{field} 不是整数（读到 {type(value).__name__}）",
                hint="真件类型树里 Count 是 unsigned int —— 0 是合法取值，⛔ 产品不替你填",
            )
        if int(value) < 0:
            raise AssetError(
                f"{where}.{field} 是负数（读到 {int(value)}）",
                hint="unsigned int 不可能是负数 —— 写进去游戏侧会读成一个巨大的数",
            )
        return int(value)
    if not isinstance(value, str):
        raise AssetError(
            f"{where}.{field} 不是字符串（读到 {type(value).__name__}）",
            hint="真件类型树里 Trigger / DescriptionLocKey 都是 string",
        )
    return value


def make_trigger(*, trigger: str, description_loc_key: str, count: int) -> dict[str, Any]:
    """**纯函数**：拼一条 UnlockTriggersData（**三格全要**，⛔ 不造默认值）。

    字段名与顺序以真件类型树为准；产品只管这三格，游戏以后加字段也不由这里补 ——
    已有条目多出来的键走 update_trigger 时**原样留着**（见下）。
    """
    return {
        TRIGGER_FIELD: _trigger_value(TRIGGER_FIELD, trigger, where="新增触发器"),
        TRIGGER_LOC_KEY_FIELD: _trigger_value(
            TRIGGER_LOC_KEY_FIELD, description_loc_key, where="新增触发器"
        ),
        TRIGGER_COUNT_FIELD: _trigger_value(TRIGGER_COUNT_FIELD, count, where="新增触发器"),
    }


def add_trigger(
    item: SkinItem, *, trigger: str, description_loc_key: str, count: int
) -> SkinItem:
    """**纯函数**：给这条皮肤**新增**一条解锁触发器 —— 条数 N -> N+1（追加在末尾）。

    追加而不是插队：既有条目的下标一个都不动（两个动作各自可撤，AGENTS §0.10）。
    """
    row = make_trigger(trigger=trigger, description_loc_key=description_loc_key, count=count)
    return replace(item, triggers=(*item.triggers, row))


def remove_trigger(item: SkinItem, *, index: int) -> SkinItem:
    """**纯函数**：**删除**第 index 条触发器 —— 条数 N -> N-1（越界 ⇒ 抛）。"""
    position = int(index)
    trigger_at(item, position)  # 越界在这里抛（带既有项清单）
    return replace(item, triggers=(*item.triggers[:position], *item.triggers[position + 1 :]))


def update_trigger(
    item: SkinItem,
    *,
    index: int,
    trigger: str | None = None,
    description_loc_key: str | None = None,
    count: int | None = None,
) -> SkinItem:
    """**纯函数**：**修改**第 index 条触发器的字段值 —— 条数不变（等长档）。

    只动给出来的那几格（没给 = 原样）；这一条上**多出来的键原样留着** ——
    游戏以后给 UnlockTriggersData 加字段，不会被我们吃掉。
    一格都没给、或改完逐字段相同 ⇒ 抛（空改动不该写盘）。
    """
    given = {
        TRIGGER_FIELD: trigger,
        TRIGGER_LOC_KEY_FIELD: description_loc_key,
        TRIGGER_COUNT_FIELD: count,
    }
    changes = {field: value for field, value in given.items() if value is not None}
    if not changes:
        raise AssetError(
            f"修改 Id={item.item_id} 的第 {int(index)} 条触发器却一格都没给 ⇒ 空改动",
            hint=f"至少给一格：{' / '.join(TRIGGER_FIELDS)}",
        )
    position = int(index)
    current = trigger_at(item, position)
    row = copy.deepcopy(dict(current))
    for field, value in changes.items():
        row[field] = _trigger_value(field, value, where=f"触发器[{position}]")
    if row == dict(current):
        raise AssetError(
            f"修改 Id={item.item_id} 的第 {position} 条触发器之后逐字段**没有任何变化** ⇒ 空改动",
            hint=f"现在就是 {dict(current)}；新旧相同不该写盘",
        )
    return replace(
        item, triggers=(*item.triggers[:position], row, *item.triggers[position + 1 :])
    )


# --------------------------------------------------------------------------- #
# 读侧：一个对象 -> (typetree, 原字节) ＋ 往返自证
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ConfigObject:
    """一个真件 UserItemsConfig 对象的读侧快照（data.unity3d 里那一个）。"""

    container: Path
    node_index: int
    node_path: str
    path_id: int
    type_id: int
    class_id: int
    name: str
    unit_count: int
    tree: Mapping[str, Any]
    nodes: tuple[clone.TypeNode, ...]
    payload: bytes
    #: 对象在**解压流**里的绝对起点（node.offset + data_offset + stored）。
    abs_start: int

    @property
    def size(self) -> int:
        return len(self.payload)


def _row_of_object(info: ufs.SerializedFileInfo, path_id: int) -> tuple[int, int, int]:
    for _index, pid, stored, size, type_id in info.objects:
        if int(pid) == int(path_id):
            return int(stored), int(size), int(type_id)
    nearby = sorted(int(o[1]) for o in info.objects)
    raise AssetError(
        f"{NODE_PATH} 的对象表里没有 pathID={int(path_id)}",
        hint=f"这个节点有 {len(info.objects):,} 个对象；读错了节点 / 拿错了 pathID"
        f"（⛔ 不要拿相近的顶替；最小的一批 pathID = {nearby[:6]}）",
    )


def read_container_config(
    container: str | Path,
    path_id: int,
    *,
    nodes: Sequence[clone.TypeNode] | None = None,
    node_path: str = NODE_PATH,
) -> ConfigObject:
    """**只读**：开容器 -> 定位对象 -> 按字段树解成 typetree，并**自证往返逐字节相同**。

    往返不可复现的对象一律拒绝（拿它写回会把没打算动的字段一起写坏）。
    """
    src = Path(container)
    tree_nodes = tuple(nodes) if nodes is not None else type_tree()
    with ufs.UfsReader(src) as reader:
        node = reader.node(node_path)
        info = ufs.read_serialized_file(reader, node)
        stored, size, type_id = _row_of_object(info, int(path_id))
        payload = reader.read_range(node.offset + info.data_offset + stored, size)
        tree = clone.read_object(payload, tree_nodes)
        if clone.write_object(tree, tree_nodes) != payload:
            raise AssetError(
                f"pathID={int(path_id)} 的 typetree 往返不是逐字节相同",
                hint="这个对象与手上的字段树（user_items.type_tree()）不符 ⇒ 写回会改坏别的字段，"
                "本模块不许动它。先跑 _work/uservis/probe_h2_tree.py 复核那棵树",
            )
        name = str(tree.get("m_Name") or "")
        if name != CONFIG_NAME:
            raise AssetError(
                f"pathID={int(path_id)} 的 m_Name 是 {name!r}，不是 {CONFIG_NAME!r}",
                hint="拿错了对象 —— 本模块只写 UserItemsConfig 那一颗",
            )
        try:
            count = len(unit_ids(tree))
        except AssetError:
            count = -1
        return ConfigObject(
            container=src,
            node_index=reader.nodes.index(node),
            node_path=node_path,
            path_id=int(path_id),
            type_id=type_id,
            class_id=ufs.MONO_BEHAVIOUR_CLASS_ID,
            name=name,
            unit_count=count,
            tree=tree,
            nodes=tree_nodes,
            payload=payload,
            abs_start=node.offset + info.data_offset + stored,
        )


def serialize_config(obj: ConfigObject, tree: Mapping[str, Any]) -> bytes:
    """按**同一棵树**把新 typetree 序列化成字节（并自证序列化可复现）。"""
    payload = clone.write_object(tree, obj.nodes)
    back = clone.read_object(payload, obj.nodes)
    if clone.write_object(back, obj.nodes) != payload:
        raise AssetError(
            f"pathID={obj.path_id} 的新字节能读回、但读回再写一遍不是同一串",
            hint="序列化不可复现 ⇒ 这一版不许写回",
        )
    return payload


def read_unit_items(
    container: str | Path,
    path_id: int,
    unit_id: int,
    *,
    nodes: Sequence[clone.TypeNode] | None = None,
) -> tuple[SkinItem, ...]:
    """**只读**：从盘上把某个单位的皮肤条目读回来（写后复核 / 判据一行用）。"""
    return skin_items(read_container_config(container, path_id, nodes=nodes).tree, unit_id)


# --------------------------------------------------------------------------- #
# 包级计划（干跑）
# --------------------------------------------------------------------------- #
def _changed_spans(old: bytes, new: bytes) -> tuple[tuple[int, int], ...]:
    """**纯函数**：两串**等长**字节差在哪几段（[start, end)、按顺序、相邻已并）。"""
    if len(old) != len(new):
        raise AssetError(
            f"两串字节长度不等：{len(old):,} != {len(new):,}",
            hint="这是变长档；只有等长档才比段",
        )
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for index, (left, right) in enumerate(zip(old, new, strict=True)):
        if left != right:
            if start is None:
                start = index
        elif start is not None:
            spans.append((start, index))
            start = None
    if start is not None:
        spans.append((start, len(old)))
    return tuple(spans)


@dataclass(frozen=True, slots=True)
class UserItemsPlan:
    """一次可见性层改动的干跑结果（⛔ 一个字节都没写）。"""

    operation: str
    container: Path
    node_path: str
    path_id: int
    config_name: str
    unit_id: int
    items_before: tuple[SkinItem, ...]
    items_after: tuple[SkinItem, ...]
    touched: tuple[int, ...]
    old_payload: bytes
    payload: bytes
    abs_start: int
    changed_spans: tuple[tuple[int, int], ...] = ()
    unit_count: int = -1

    @property
    def delta(self) -> int:
        return len(self.payload) - len(self.old_payload)

    @property
    def level(self) -> str:
        return "等长（原地覆盖）" if self.delta == 0 else "变长（平移后续对象）"

    @property
    def changed_bytes(self) -> int:
        return sum(end - start for start, end in self.changed_spans)

    def replacement(self) -> ufs.Replacement:
        return ufs.Replacement(
            self.abs_start, self.abs_start + len(self.old_payload), self.payload
        )

    def count_line(self) -> str:
        """**判据一行**：条目数 N -> M。"""
        return (
            f"单位 {self.unit_id} 的皮肤条目 {len(self.items_before)} -> {len(self.items_after)}"
            f"（{self.operation}，Id {list(self.touched)}）"
        )

    def criterion_lines(self) -> tuple[str, ...]:
        if self.delta == 0:
            spans = "、".join(f"{lo}~{hi}" for lo, hi in self.changed_spans) or "（无差异段）"
            detail = (
                f"等长档：改动 {self.changed_bytes} 字节（{len(self.changed_spans)} 段）：{spans}"
            )
        else:
            detail = f"变长档 {self.delta:+,} 字节：⛔ 不比段（后续对象的 byte_start 会整体平移）"
        return (
            f"[可见性{self.operation}] {Path(self.container).name} / {self.node_path}"
            f" pathID={self.path_id}（m_Name={self.config_name!r}，"
            f"Skins 共 {self.unit_count} 个单位）",
            f"  {self.count_line()}",
            f"  对象字节 {len(self.old_payload):,} -> {len(self.payload):,} B"
            f"（{self.delta:+,}）  {detail}",
            f"  替换区间 = 解压流 [{self.abs_start:,}, {self.abs_start + len(self.old_payload):,})",
        )

    def summary(self) -> str:
        return (
            f"{Path(self.container).name}：单位 {self.unit_id} 皮肤表{self.operation} "
            f"Id {list(self.touched)}（{len(self.items_before)} -> {len(self.items_after)} 条）"
        )

    def patch_label(self) -> str:
        """补丁柜里那一条的标签 —— assets status 会**照原样**印出来，所以写人话。"""
        return (
            f"可见性层{self.operation}：{Path(self.container).name} "
            f"pathID={self.path_id} 单位 {self.unit_id}"
            f"（{len(self.items_before)} -> {len(self.items_after)} 条）"
        )


def _touch(before: Sequence[SkinItem], after: Sequence[SkinItem]) -> tuple[int, ...]:
    old = {item.item_id: item for item in before}
    new = {item.item_id: item for item in after}
    return tuple(key for key in sorted(set(old) | set(new)) if old.get(key) != new.get(key))


def _make_plan(
    container: str | Path,
    path_id: int,
    *,
    operation: str,
    unit_id: int,
    apply: Any,
    nodes: Sequence[clone.TypeNode] | None = None,
) -> UserItemsPlan:
    obj = read_container_config(container, path_id, nodes=nodes)
    before = skin_items(obj.tree, unit_id)
    new_tree = apply(obj.tree)
    after = skin_items(new_tree, unit_id)
    if after == before:
        raise AssetError(
            f"{operation}之后条目逐字段没有任何变化 ⇒ 空改动",
            hint="空改动不该写盘：检查一下是不是把同一件事做了两遍",
        )
    payload = serialize_config(obj, new_tree)
    got = skin_items(clone.read_object(payload, obj.nodes), unit_id)
    if got != after:
        raise AssetError(
            f"写出来的字节读回去是 {len(got)} 条，期望 {len(after)} 条",
            hint="序列化没把新表写进去 —— 这一版不许写回",
        )
    if payload == obj.payload:
        raise AssetError(
            f"改完的字节与原字节**一模一样** ⇒ 空改动（{operation}）",
            hint="空改动不该写盘：检查一下是不是把同一件事做了两遍",
        )
    spans = _changed_spans(obj.payload, payload) if len(payload) == len(obj.payload) else ()
    return UserItemsPlan(
        operation=operation,
        container=obj.container,
        node_path=obj.node_path,
        path_id=obj.path_id,
        config_name=obj.name,
        unit_id=int(unit_id),
        items_before=before,
        items_after=after,
        touched=_touch(before, after),
        old_payload=obj.payload,
        payload=payload,
        abs_start=obj.abs_start,
        changed_spans=spans,
        unit_count=obj.unit_count,
    )


def plan_add(
    container: str | Path,
    path_id: int,
    *,
    unit_id: int,
    item: SkinItem,
    reference_id: int,
    nodes: Sequence[clone.TypeNode] | None = None,
) -> UserItemsPlan:
    """**干跑**：新增一条可见性条目（照抄参照条）—— 条目数 N -> N+1。"""
    return _make_plan(
        container,
        path_id,
        operation="新增",
        unit_id=unit_id,
        apply=lambda tree: add_skin_item(
            tree, unit_id=unit_id, item=item, reference_id=reference_id
        ),
        nodes=nodes,
    )


def plan_remove(
    container: str | Path,
    path_id: int,
    *,
    unit_id: int,
    item_id: int,
    nodes: Sequence[clone.TypeNode] | None = None,
) -> UserItemsPlan:
    """**干跑**：删除一条可见性条目 —— 条目数 N -> N-1。"""
    return _make_plan(
        container,
        path_id,
        operation="删除",
        unit_id=unit_id,
        apply=lambda tree: remove_skin_item(tree, unit_id=unit_id, item_id=item_id),
        nodes=nodes,
    )


def plan_update(
    container: str | Path,
    path_id: int,
    *,
    unit_id: int,
    item_id: int,
    changes: Mapping[str, Any],
    nodes: Sequence[clone.TypeNode] | None = None,
    operation: str | None = None,
) -> UserItemsPlan:
    """**干跑**：修改一条可见性条目的既有字段（一条皮肤的字段、或它的一条触发器）。

    operation 是判据 / 补丁标签里那个**动作名**（默认「修改」）—— CLI 在触发器
    那一支点名「新增触发器 / 删除触发器 / 修改触发器」（那是动作的名字，不是新动作）。
    """
    return _make_plan(
        container,
        path_id,
        operation=operation or "修改",
        unit_id=unit_id,
        apply=lambda tree: update_skin_item(
            tree, unit_id=unit_id, item_id=item_id, **dict(changes)
        ),
        nodes=nodes,
    )


# --------------------------------------------------------------------------- #
# 写侧：install（唯一入口）＋ 写后复核 ＋ 反向 Δ
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ReverseDelta:
    """反向 Δ 的一条：**只存被替换那一段的原始字节**（ADR 0007，⛔ 不拷整包）。

    container = **要回滚的那一份**（install 的产物；没装过时就是干跑读的那份源文件）；
    origin    = 干跑时读的源文件（补丁柜记它是为了说清「这份改动是从哪儿来的」）。
    """

    container: Path
    origin: Path
    node_path: str
    path_id: int
    unit_id: int
    operation: str
    old_bytes: bytes
    new_size: int

    def record(self) -> dict[str, Any]:
        return {
            "container": str(self.container),
            "origin": str(self.origin),
            "node": self.node_path,
            "path_id": self.path_id,
            "unit_id": self.unit_id,
            "operation": self.operation,
            "old_size": len(self.old_bytes),
            "new_size": self.new_size,
        }

    def label(self) -> str:
        return (
            f"可见性层{self.operation}：{Path(self.origin).name} pathID={self.path_id} "
            f"单位 {self.unit_id}（{len(self.old_bytes):,} -> {self.new_size:,} B）"
        )


def reverse_delta(plan: UserItemsPlan) -> ReverseDelta:
    """把一次干跑结果收成一条可还原的反向 Δ（⛔ 一个字节都没写）。"""
    return ReverseDelta(
        container=plan.container,
        origin=plan.container,
        node_path=plan.node_path,
        path_id=plan.path_id,
        unit_id=plan.unit_id,
        operation=plan.operation,
        old_bytes=plan.old_payload,
        new_size=len(plan.payload),
    )


@dataclass(frozen=True, slots=True)
class InstallResult:
    """一次写回的结果：产物路径 ＋ writer 读数 ＋ 反向 Δ ＋ 写后复核行。

    patch_path 非空 = 这一笔已经**落进补丁柜**（另起一个进程也撤得回来）；
    它是空的时候**别拿产物去装机** —— 那就是「撤不回来」的现场。
    """

    target: Path
    report: ufs.WriteReport
    reverse: ReverseDelta
    lines: tuple[str, ...]
    patch_path: Path | None = None
    patch_bytes: int = 0
    patch_lines: tuple[str, ...] = ()


def _read_all_objects(container: str | Path, node_path: str) -> dict[int, bytes]:
    with ufs.UfsReader(container) as reader:
        node = reader.node(node_path)
        info = ufs.read_serialized_file(reader, node)
        return {
            int(pid): reader.read_range(node.offset + info.data_offset + stored, size)
            for _index, pid, stored, size, _type_id in info.objects
        }


def verify_install(plan: UserItemsPlan, target: str | Path) -> tuple[str, ...]:
    """**写后复核**：从产物里把那个对象读回来逐条比 ＋ 同节点其它对象逐字节未变。

    判据（J3 的读数就是它）：条目数 N -> M（读回 M）；任一不满足 ⇒ 抛。
    """
    out = Path(target)
    got = read_unit_items(out, plan.path_id, plan.unit_id)
    if got != plan.items_after:
        raise AssetError(
            f"写后读回 {len(got)} 条，期望 {len(plan.items_after)} 条",
            hint="读回：" + str([i.summary() for i in got]),
        )
    before = _read_all_objects(plan.container, plan.node_path)
    after = _read_all_objects(out, plan.node_path)
    if set(before) != set(after):
        raise AssetError(
            f"写后对象表变了：改前 {len(before)} 个 → 改后 {len(after)} 个",
            hint="本模块只改一个对象的字节，⛔ 不许增删对象表条目",
        )
    changed = sorted(pid for pid in before if before[pid] != after[pid])
    if changed != [plan.path_id]:
        raise AssetError(
            f"写后除了 pathID={plan.path_id} 之外还有对象变了：{changed[:8]}",
            hint="只该动一个对象 —— 说明替换区间压到了别的东西",
        )
    return (
        f"写后复核：单位 {plan.unit_id} 皮肤条目 {len(plan.items_before)} -> "
        f"{len(plan.items_after)}（读回 {len(got)}，逐条与期望相同）",
        f"  同节点其它 {len(before) - 1:,} 个对象逐字节未变（只有 pathID={plan.path_id} 变了）",
    )


def _record_patch(
    plan: UserItemsPlan,
    product: Path,
    *,
    store: Any,
    patch_product: Path,
    sha_before: str,
) -> tuple[Path, int, tuple[str, ...]]:
    """把这一次写回收成补丁柜里的一条 **range** 反向 Δ（ADR 0007：⛔ 不拷整包）。

    ⚠ sha_before 必须是**写之前**算的那一份：产物与源件同名（--in-place）时，
    写完之后源件就是产物，再怎么算都算不出「装前」。
    ⚠ patch_product 是产物**最终**会在哪（--in-place 那条链写的是临时文件、随后改名就位）。
    """
    sha_after = rdelta.sha256_of(product)
    # 装的那一刻**会被重切**的那一段（装后坐标）—— 与 rewrite_container 算 dirty run 同一套算式。
    # 还原时按它复现同一条块边界：不复现，装后文件的块边界就与原始文件不同，
    # 整文件 sha256 永远回不去（BUG-2：data.unity3d「撤不掉」的根因）。
    # ⚠ 这里读的必须是**装前**那一份的块表 —— plan.container 此刻还没被改名就位。
    with ufs.UfsReader(plan.container) as reader:
        run_start, run_end = ufs.rewrite_run(reader, plan.replacement(), delta=plan.delta)
    builder = store.begin(plan.patch_label())
    target = builder.capture_range(
        path=patch_product,
        node_path=plan.node_path,
        path_id=plan.path_id,
        stream_start=plan.abs_start,
        old_bytes=plan.old_payload,
        new_size=len(plan.payload),
        label=plan.patch_label(),
        sha_before=sha_before,
        sha_after=sha_after,
        size_before=plan.container.stat().st_size,
        size_after=product.stat().st_size,
        run_start=run_start,
        run_end=run_end,
    )
    builder.self_check_range(target, live_path=product)
    patch_path = builder.commit()
    # 写出去再**从盘上读回来**（store.pending() 会顺手校验 blob 的 sha256）——
    # 这一步才是「另起一个进程读得回来」的机械保证，不是"我以为写进去了"。
    patch = store.pending()
    lines: tuple[str, ...] = () if patch is None else patch.lines()
    patch_bytes = patch_path.stat().st_size + store.blob_path.stat().st_size
    return patch_path, patch_bytes, lines


def install(
    plan: UserItemsPlan,
    target: str | Path,
    *,
    progress: Any = None,
    token: Any = None,
    patch_store: Any = None,
    patch_product: str | Path | None = None,
) -> InstallResult:
    """执行一次可见性层改动：把那一颗对象的字节写进**新文件** target（⛔ 不动源文件）。

    * 落点 = 那一个对象的解压流区间（等长 / 变长都走同一套：ufs.rewrite_container
      自己负责元数据补丁、节点表、块重切）；
    * 写完立刻做**写后复核**；任何一条不过 ⇒ 删掉半成品再抛；
    * patch_store 给了就**顺手把这一笔落进补丁柜**（range 反向 Δ，ADR 0007：
      ⛔ 不拷整包）。它是「另起一个进程还撤得回来」的唯一来源 ⇒ 落不成时
      **连产物一起删掉**（⛔ 不留一个撤不回来的现场）。
    """
    out = Path(target)
    if out == plan.container:
        raise AssetError(
            "输出路径与源文件相同",
            hint="写回必须写到别的文件，再由调用方原子替换；否则失败会把游戏文件毁掉",
        )
    sha_before = rdelta.sha256_of(plan.container) if patch_store is not None else ""
    with ufs.UfsReader(plan.container) as reader:
        report = ufs.rewrite_container(
            reader, [plan.replacement()], out, progress=progress, token=token
        )
    try:
        lines = verify_install(plan, out)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    patch_path: Path | None = None
    patch_bytes = 0
    patch_lines: tuple[str, ...] = ()
    if patch_store is not None:
        try:
            patch_path, patch_bytes, patch_lines = _record_patch(
                plan,
                out,
                store=patch_store,
                patch_product=Path(patch_product) if patch_product is not None else out,
                sha_before=sha_before,
            )
        except BaseException:
            out.unlink(missing_ok=True)
            raise
    # ⚠ 反向 Δ 的 container 指向**产物**：还原的含义是「把装过的那份滚回原样」。
    return InstallResult(
        target=out,
        report=report,
        reverse=replace(reverse_delta(plan), container=out),
        lines=lines,
        patch_path=patch_path,
        patch_bytes=patch_bytes,
        patch_lines=patch_lines,
    )


def restore(
    delta: ReverseDelta,
    target: str | Path,
    *,
    progress: Any = None,
    token: Any = None,
) -> ufs.WriteReport:
    """按一条反向 Δ 还原（写到**新文件** target）。

    ⚠ 不信任记录里的绝对偏移：按 pathID **重新定位**（源文件若被别的东西动过，
    偏移会变；记下来的旧偏移会把原字节写到错的地方）。定位不到 / 长度对不上 ⇒ 抛。
    """
    out = Path(target)
    if out == delta.container:
        raise AssetError(
            "输出路径与源文件相同",
            hint="还原也必须写到别的文件，再由调用方原子替换",
        )
    with ufs.UfsReader(delta.container) as reader:
        node = reader.node(delta.node_path)
        info = ufs.read_serialized_file(reader, node)
        stored, size, _type_id = _row_of_object(info, delta.path_id)
        if size != delta.new_size:
            raise AssetError(
                f"pathID={delta.path_id} 现在的字节数是 {size:,}，"
                f"反向 Δ 记的是 {delta.new_size:,}",
                hint="源文件已经不是我改动之后那一版了 —— 先核对现场再还原，⛔ 别硬写",
            )
        start = node.offset + info.data_offset + stored
        rep = ufs.Replacement(start, start + size, delta.old_bytes)
        return ufs.rewrite_container(reader, [rep], out, progress=progress, token=token)
