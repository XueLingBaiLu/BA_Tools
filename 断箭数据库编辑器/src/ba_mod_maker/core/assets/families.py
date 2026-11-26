"""图标族的定义：一族图标住在哪个 bundle、键怎么归一化。

这份表是**运行时解析**与 tools/extract_icons.py 共用的领域知识，
放在 core 里是因为它零 Qt（见 tests/test_core_layering.py）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

#: 空心版地址的前缀。**实测**：实心/空心在 catalog 里是两条独立地址——
#: 裸键 AMMO_KONKURS_M 指向 Icons/ 下的实心图，
#: outline\AMMO_KONKURS_M 指向 Icons/outline/ 下的空心线稿。
#: 全表有 1461 个 outline 前缀键。DB 里存的都是裸键 = 实心。
OUTLINE_PREFIX = "outline" + chr(92)

#: 资产路径里表示"空心版"的那一段。
OUTLINE_SEGMENT = "/outline/"


@dataclass(frozen=True, slots=True)
class IconFamily:
    """一族图标。

    属性
    ----
    name
        族名，与 cache/icons 的子目录名、界面里的 family 参数一致。
    label
        中文说明（日志与测试用）。
    bundle_glob
        主包的 glob。**不能写死哈希**：游戏更新后包名里的哈希会变。
    column
        这一族的键来自哪一列（表.列），用来从 _external_full.json 里
        补充**主包之外**的包——实测单位缩略图有 88/464 个键不在主包里。
    """

    name: str
    label: str
    bundle_glob: str
    column: str = ""
    #: 预取顺序权重，**越小越先取**。预取的包要按「提供它的包」分组（见
    #: IconResolver.plan），组的先后就按这个权重排：首屏看得见的族排前面，
    #: 用户点「跳过」之后也能先看到该看的图。
    priority: int = 50
    #: **不来自数据库列**的常量地址键。两种场合：
    #: ① 这一族压根没有键列 —— 例：目标类型图标，弹药表里没有这一列，
    #:    键是 InfocardConfig 里写死的 5 个 sprite；
    #: ② 有键列、但有一格的值**表里查不到** —— 例：国旗族的「未分类」那一格
    #:    （见 UNCLASSIFIED_FLAG_KEY）。
    keys: tuple[str, ...] = ()
    #: 这一族的原图是不是**朝左**、界面要水平翻一次才与游戏一致。
    #:
    #: 用户口径（2026-09-30 报、2026-10-01 又报「还是老问题，武器能不能让它们朝右」）：
    #: 「武器在 GUI 里是朝左边的，应该朝右边才是」。判据：原图 `INF_AK74RMO.png`
    #: （231×82）**枪口朝左**，而实机紧凑卡上画的是**朝右** —— 同一张素材，
    #: 游戏在 UI 里水平翻了一次，所以我们也得翻。
    #: ⚠️ **弹药那一族（子弹 174×36）本来就朝右，别一起翻**；其余族一律 False。
    #: 翻转只有这一份出处：界面统一走 `app/theme/icons.py::prepare_icon`。
    mirrored: bool = False


#: 目标类型图标住的那一个目录（5 张图的共同前缀，写一遍省得每行都超宽）。
TARGET_ICON_DIR = "Assets/Prefabs/GUI/HUD/Images/Infocard/Target type icons/"

#: 目标类型图标的地址键：TargetType 的位名 -> InfocardConfig 里那个 sprite 的地址。
#: 出处：AmmoInfoExpansionPanel.SetTargetTraits 读的 sprite 偏移 —— 0x1C8 步兵 /
#: 0x1D0 载具 / 0x1D8 直升机 / 0x1E0 固定翼 / 0x1E8 导弹，与上面那个目录里的 5 张图一一对应。
#: ❗ 位名照 dump.cs 的 TargetType：Ground=1, Infantry=2, Vehicle=4, Helicopter=8,
#:   Aircraft=16, Ship=32 —— **1 是 Ground 不是步兵**（列清单里那句旧说明写错过）。
TARGET_ICON_KEYS: Mapping[str, str] = {
    "infantry": TARGET_ICON_DIR + "Target Type Infantry Icon.png",
    "vehicle": TARGET_ICON_DIR + "Target Type Vehicles Icon.png",
    "helicopter": TARGET_ICON_DIR + "Target Type Helicopters Icon.png",
    "aircraft": TARGET_ICON_DIR + "Target Type Aircrafts Icon.png",
    "missiles": TARGET_ICON_DIR + "Target Type Missiles Icon.png",
}


#: 改装槽（`Modifications`）那一行的**按钮图标**。键不在任何数据列里 ——
#: 那张表的列是 Id/Name/Order/ThumbnailFileName/Type/UIName/UnitId，而
#: `ThumbnailFileName` **全表为空**（实测 615 行：612 个 None + 3 个空串），
#: 游戏自己的 `Modifications` 类也只声明了字段、反编译出来的 UI 一处都不读它。
#:
#: 出处（反编译 `_work/cpp2il_cs/.../BrokenArrow/Client/Ecs/UI/IconsLoader.cs`）：
#:   `public static Sprite LoadModificationButtonIcon(ModificationType modType, string name, ...)`
#:   + `private static readonly string _modButtonsPath;`
#: 全游戏**只有两张**可寻址的改装按钮图（catalog 里 `Infocard/Modification icons/` 就两条）：
#:   盾牌 = Armor 槽（用户 2026-10-01 给的实机「改装栏」截图里，装甲组件那一行画的就是它），
#:   齿轮 + 扳手 = 其余所有槽的通用那颗。
#: `ModificationType` 枚举（同一份反编译）：
#:   None=0 General=1 Mobility=2 Turret=3 Sensor=4 Armor=5 Ability=6 UnitReplace=7。
MOD_SLOT_ICON_DIR = "Assets/Prefabs/GUI/HUD/Images/Infocard/Modification icons/"
MOD_SLOT_ARMOR_ICON = MOD_SLOT_ICON_DIR + "Modification Armor Slot Icon.png"
MOD_SLOT_GENERIC_ICON = MOD_SLOT_ICON_DIR + "Modification Icon.png"

#: `Modifications.Type` -> 地址键。**只登记不通用那一颗**（Armor=5），其余走 fallback。
MOD_SLOT_ICON_KEYS: Mapping[int, str] = {5: MOD_SLOT_ARMOR_ICON}

#: 没登记的类型用哪一张。游戏也只有这两张，所以改装槽**每一行都有图**（不是"没图"）。
MOD_SLOT_ICON_FALLBACK = MOD_SLOT_GENERIC_ICON

#: 数值行图标的地址键：**InfocardConfig 的字段名 -> sprite 地址**。
#:
#: 出处与证据：
#:  1) 字段名来自反编译的 `InfocardConfig`（`_work/ilspy_cs/BrokenArrow.decompiled.cs:90842`，
#:     声明顺序就是卡片的图标顺序）；
#:  2) 每个字段指向哪张图，用**用户给的实机卡片截图**逐格对过（步兵那张卡 8 格：
#:     盾=Armor / 心+十字=HealthInfantry / 三个人=SoldierCount / 望远镜=Optics /
#:     划掉的眼睛=Stealth / 奔跑的士兵=SpeedForwardInfantry / 砝码=Weight / 骷髅时钟=RefundTime）；
#:  3) 其余按同一套命名规则（.inf 步兵 / .car 载具 / .hel 直升机 / .air 固定翼），
#:     还没有截图逐格核对 —— 见 docs/研究-单位卡片公式.md。
#: ✅ `RefundTime` 对的图是**骷髅时钟** `DeathTimer.png`（不是名字更像的 `RefundTimer.png`）：
#:   **那一格显示的就是"阵亡后"的回转时间**（`_t_refund` 用的就是 REFUND_IS_DEAD），
#:   所以画死亡时钟是语义正确的，不是巧合 —— 用户确认过。
STAT_ICON_DIR = "Assets/Prefabs/GUI/HUD/Images/Infocard/General icons/"
TARGET_STAT_SPRITES: Mapping[str, str] = {
    "Armor": "Armor.png",
    "Health": "Health points.car.png",
    "HealthInfantry": "Health points.inf.png",
    "SoldierCount": "group.png",
    "HeavyLift": "Load capacity.png",
    "SeatsCount": "Seats.png",
    "Optics": "Optics.png",
    "Stealth": "Stealth.png",
    "SpeedForwardVehicles": "Forward speed.car.png",
    "SpeedBackwardVehicles": "Speed backwards.car.png",
    "SpeedForwardHelicopters": "Forward speed.hel.png",
    "SpeedForwardAircrafts": "Forward speed.air.png",
    "SpeedForwardInfantry": "Forward speed.inf.png",
    "AgilityHelicopters": "Agility turn rate.hel.png",
    "AgilityPlanes": "Agility turn rate.air.png",
    "Fuel": "Fuel.png",
    "Weight": "Weight.png",
    "RefundTime": "DeathTimer.png",
}

#: InfocardConfig 字段名 -> 完整地址键（族里用的就是这一份）。
STAT_ICON_KEYS: Mapping[str, str] = {
    name: STAT_ICON_DIR + leaf for name, leaf in TARGET_STAT_SPRITES.items()
}


#: 族名常量 —— **字面量只在这一个文件里出现**。
#: ❗ 曾经 `"unit_labels"` / `"weapon_icons"` / `"ammo_icons"` / `"option_pictures"` 这几串
#:    在 6 个界面文件里各抄了一遍（armory/detail_panel、armory/unit_card、editor 的三个页…）：
#:    抄错一个字母的后果是"这一格永远没图标"，而且不报错。
UNIT_LABEL_FAMILY = "unit_labels"
UNIT_PORTRAIT_FAMILY = "unit_portraits"
WEAPON_ICON_FAMILY = "weapon_icons"
AMMO_ICON_FAMILY = "ammo_icons"
OPTION_PICTURE_FAMILY = "option_pictures"
FLAG_FAMILY = "flags"
SPEC_ICON_FAMILY = "spec_icons"
SPEC_ILLUSTRATION_FAMILY = "spec_illustrations"
STAT_ICON_FAMILY = "infocard_stats"
TARGET_ICON_FAMILY = "infocard_targets"
MOD_SLOT_ICON_FAMILY = "mod_slot_icons"


#: **表 -> (族, 键列)**：中栏标题 / 面包屑上那一行该画哪张**游戏里的真图标**。
#: 唯一一份 —— 界面别自己拼族名与列名（见上面那排常量的由来）。
#:
#: ⛔ 这里只回答"画哪张图"，**取图**必须走 `IconStore.pixmap`（bundle 直读，见 ADR 0003）；
#:    直接读 `cache/icons` 下的 PNG 是**没装游戏**时的兜底，而且旧提取把 151 张取成了
#:    outline 空心版（用户报过的 Konkurs）—— ADR 0003 §决定 4 写死了这条。
#:
#: 为什么 `Modifications` 不在表里：它**没有可用的键列**（实测列是
#: Id/Name/Order/ThumbnailFileName/Type/UIName/UnitId，而 ThumbnailFileName 全表为空）
#: —— 它的图走**枚举那条路**（`ENUM_ICON_SOURCES`：Type -> MOD_SLOT_ICON_KEYS），见下面。
ROW_ICON_SOURCES: Mapping[str, tuple[str, str]] = {
    "Units": (UNIT_LABEL_FAMILY, "ThumbnailFileName"),
    "Weapons": (WEAPON_ICON_FAMILY, "HUDIcon"),
    "Ammunitions": (AMMO_ICON_FAMILY, "HUDIcon"),
    "Options": (OPTION_PICTURE_FAMILY, "OptionPicture"),
}


def row_icon_source(table: str) -> tuple[str, str] | None:
    """这一行的图标来自哪一族、哪一列：`(族名, 键列)`；没登记的返回 None。

    返回的族名**一定**是 `FAMILIES` 里注册过的（test_row_icon.py 守着这条不漂）。
    """
    return ROW_ICON_SOURCES.get(table)


#: 「键不在列里、要**按这一行的枚举值**去常量表查」的族：表 -> (族名, 枚举列, 值->键, 兜底键)。
#: 现在只有改装槽一条（`Type` 是 ModificationType，图只有装甲 / 通用两张）。
#: 与 ROW_ICON_SOURCES 的区别：那一条是"整列就是一个键"，这一条是"列是枚举、键要查表"。
ENUM_ICON_SOURCES: Mapping[str, tuple[str, str, Mapping[int, str], str]] = {
    "Modifications": (MOD_SLOT_ICON_FAMILY, "Type", MOD_SLOT_ICON_KEYS, MOD_SLOT_ICON_FALLBACK),
}


def enum_icon_source(table: str) -> tuple[str, str, Mapping[int, str], str] | None:
    """这一行的图标来自**枚举列**时返回 (族名, 列名, 值->键, 兜底键)；没有就 None。"""
    return ENUM_ICON_SOURCES.get(table)


#: 「未分类」那一格用的旗子（不属于任何国家的单位）。
#:
#: ⛔ **不是 DB 里的键**：Countries 表只有 3 行（1 Russia / 2 USA / 3 Editor），而 Editor 那条
#: `FlagFileName` 是**空的** —— 这面旗没有 DB 键，只能按资产名挂成常量键。
#: ✅ 实测它就在国旗那一族同一个包里（`_work/probe_flag_assets.py`：nations&specs 包 32 个叶子里
#: 带 flag 的 6 个 —— rus flag / usa flag / rus flag 2 / flag / mixed flag / **random flag**），
#: 其中 `random flag` 就是用户说的「橙底、白问号旗」（272×128；
#: 导出件 `_work/tmp/random_flag.png`，走产品自己的解析路取到 84633 字节 PNG ——
#: `_work/probe_unclassified_flag_resolve.py`）。
UNCLASSIFIED_FLAG_KEY = "random flag"


FAMILIES: tuple[IconFamily, ...] = (
    IconFamily(
        UNIT_LABEL_FAMILY,
        "单位缩略图",
        "unitlabels_assets_all_*.bundle",
        "Units.ThumbnailFileName",
        0,
    ),
    IconFamily(
        UNIT_PORTRAIT_FAMILY,
        "单位立绘",
        "unitportraits_assets_all_*.bundle",
        "Units.PortraitFileName",
        1,
    ),
    IconFamily(
        WEAPON_ICON_FAMILY,
        "武器图标",
        "unitweaponicons_assets_all_*.bundle",
        "Weapons.HUDIcon",
        6,
        mirrored=True,  # 枪口原图朝左：实机卡上是朝右的（见 IconFamily.mirrored）
    ),
    IconFamily(
        AMMO_ICON_FAMILY,
        "弹药图标",
        "ammoicons_assets_all_*.bundle",
        "Ammunitions.HUDIcon",
        7,
    ),
    IconFamily(
        FLAG_FAMILY,
        "国旗",
        "nations&specs_assets_all_*.bundle",
        "Countries.FlagFileName",
        0,
        # 「未分类」那面问号旗不在 Countries 表里，只能当常量键挂上（预加载才认得它）。
        keys=(UNCLASSIFIED_FLAG_KEY,),
    ),
    IconFamily(
        SPEC_ICON_FAMILY,
        "专精图标",
        "nations&specs_assets_all_*.bundle",
        "Specializations.Icon",
        2,
    ),
    IconFamily(
        SPEC_ILLUSTRATION_FAMILY,
        "专精插画",
        "nations&specs_assets_all_*.bundle",
        "Specializations.Illustration",
        3,
    ),
    # 改型选项的图片（游戏「自定义选项」那一排画的就是它）。
    # 键来自 Options.OptionPicture —— 实测 2046 行 / 397 个去重键，提供者是 unitweaponicons，
    # 与武器图标同一批素材（Assets/Resources_moved/Images/Weapons/Icons/*.png）。
    #
    # ❗❗ **这一族的朝向是逐资产的，一个族级布尔量表达不了**（2026-10-03 实测）：
    #   同一屏游戏的「自定义选项」里，INF_T5000 那张画的是**镜像**、
    #   MOD_FSB_SNIPER_OSV96 那张画的是**原图** —— 两条都是强判据
    #   （IoU 0.866:0.284 与 0.647:0.157）；尺子 + 两条方向相反的正控见
    #   _work/w9c_probe_flip4.py。
    #   游戏到底画的哪张图也钉过（_work/w9c_probe_whichasset.py：0.647 最高；
    #   ThumbnailOverride 那张才 0.353）—— 画的确实是 OptionPicture，不是单位标签。
    #   ⇒ 翻不翻走 family_mirrors(name, key)，见 OPTION_PICTURE_UNMIRRORED_PREFIXES。
    #
    # 397 个键的实测分布（复算 _work/w9c_probe_shared.py：这个键同时是不是武器图标的键）：
    #   · MOD_ 47 个 —— 在 Weapons.HUDIcon 列里 **0/47**（改装界面自己的一批美术）；实测**不翻**
    #   · 其余 350 个里 **274 个同时是武器图标的键**（INF_ 68 个里 66 个；FAB/VEH/KH/TOW… 整组都是）
    #     —— 其中 INF_T5000 实测**要翻**，与武器图标那族同向
    #   · 剩下 **76 个未量**：ARTY_(9) / MLRS_(6) / MORTAR_(5) / ARMOR* / FUEL* / OPTIC* / SMOKE /
    #     ECM / LASER / TOCHKA_* / S300_* / AIM120_* / MSE_* / PAC*_* … —— 它们在游戏里画哪一面
    #     **没有实测**，所以**保持改前行为（翻）**，不猜；要扩展先补量。
    IconFamily(
        OPTION_PICTURE_FAMILY,
        "改型选项图片",
        "unitweaponicons_assets_all_*.bundle",
        "Options.OptionPicture",
        8,
        # 族级默认 = 翻（改前就是这个）；MOD_ 那批例外，见 OPTION_PICTURE_UNMIRRORED_PREFIXES。
        mirrored=True,
    ),
    # 数值行那排图标。键也不在数据列里 —— 是 InfocardConfig 的 sprite 地址
    # （见 STAT_ICON_KEYS），所以同样走 keys。
    IconFamily(
        STAT_ICON_FAMILY,
        "数值行图标",
        "prefabsgui_assets_all_*.bundle",
        priority=5,
        keys=tuple(STAT_ICON_KEYS.values()),
    ),
    # 弹药的「能打什么」那排小图标。键不在任何数据列里 —— 是 InfocardConfig 的
    # 5 个 sprite 地址（见 TARGET_ICON_KEYS），所以走 keys 而不是 column。
    IconFamily(
        TARGET_ICON_FAMILY,
        "弹药目标类型图标",
        "prefabsgui_assets_all_*.bundle",
        priority=8,
        keys=tuple(TARGET_ICON_KEYS.values()),
    ),
    # 改装槽按钮图标。键也不在数据列里 —— 按 ModificationType 查常量表（见 MOD_SLOT_ICON_KEYS）。
    IconFamily(
        MOD_SLOT_ICON_FAMILY,
        "改装槽按钮图标",
        "prefabsgui_assets_all_*.bundle",
        priority=9,
        keys=(MOD_SLOT_ARMOR_ICON, MOD_SLOT_GENERIC_ICON),
    ),
)

_BY_NAME = {entry.name: entry for entry in FAMILIES}


def family(name: str) -> IconFamily | None:
    """按族名取定义；没有这一族返回 None。"""
    return _BY_NAME.get(name)


def family_names() -> tuple[str, ...]:
    return tuple(entry.name for entry in FAMILIES)


#: `Options.OptionPicture` 里**不翻**的那些键的前缀 —— **按资产判，不是按族判**。
#:
#: 为什么要它：同一族里两条实测方向相反（出处与正控见 FAMILIES 里 option_pictures 那段）——
#:   MOD_FSB_SNIPER_OSV96 游戏画的是原图（不翻），INF_T5000 游戏画的是镜像（要翻）。
#:
#: 依据是**量出来的类边界**（_work/w9c_probe_shared.py）：MOD_ 那 47 个键在
#: Weapons.HUDIcon 列里是 **0/47** —— 它们是改装界面自己的一批美术，不是武器图标那批素材
#: （原图就是照显示朝向画的）。
#:
#: ⚠️ 这条**不覆盖**其余前缀：那 76 个未量的键行为保持改前（翻）。要加新前缀，
#: 先拿出「游戏画的是原图」的实测（同一把尺子 + 两条方向相反的正控）。
OPTION_PICTURE_UNMIRRORED_PREFIXES: tuple[str, ...] = ("MOD_",)


def family_mirrors(name: str, key: str = "") -> bool:
    """这一族的图**要不要水平翻**（界面统一走 `prepare_icon`）。

    没登记的族一律 False —— 不猜：翻转是"原图朝左、实机朝右"这条实测知识的产物，
    不是默认行为（弹药那族本来就朝右，翻了就错）。出处见 `IconFamily.mirrored`。

    `key` = 这一张图的地址键，**只有 option_pictures 用得上**（那一族逐资产判，
    见 `OPTION_PICTURE_UNMIRRORED_PREFIXES`）；其余族只认族名。

    ⚠️ **key 缺省给空串时的兜底**：按**族默认**（= 翻），与改前逐字一致 ——
    拿不到键的调用方不许被悄悄改行为；要它按资产判，就得把键传到底。
    """
    entry = _BY_NAME.get(name)
    if entry is None or not entry.mirrored:
        return False
    if name == OPTION_PICTURE_FAMILY and key:
        return not key.strip().upper().startswith(OPTION_PICTURE_UNMIRRORED_PREFIXES)
    return True


def split_address(key: str) -> tuple[bool, str]:
    """地址键 -> (是不是空心版, 叶子名)。

    实测（catalog 的键表）：

    | 键 | 指向 |
    |---|---|
    | AMMO_KONKURS_M | .../Icons/AMMO_KONKURS_M.png（实心） |
    | outline 前缀 + AMMO_KONKURS_M | .../Icons/outline/AMMO_KONKURS_M.png（空心） |
    | RU 反斜杠 BTR_82A 反斜杠 BTR_82A | 立绘的路径式键，取叶子 BTR_82A |

    ❗ **空心不是"同一张图的另一个版本"，而是另一条地址。** 早先按纹理名匹配，
    两套纹理同名，于是 151 张图标被取成了空心（用户报的 Konkurs 就是其一）。
    """
    raw = key.strip().replace("/", chr(92))
    outline = raw.lower().startswith(OUTLINE_PREFIX)
    if outline:
        raw = raw[len(OUTLINE_PREFIX) :]
    leaf = raw.split(chr(92))[-1]
    if leaf.lower().endswith(".png"):
        leaf = leaf[:-4]
    return outline, leaf


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize(key: str) -> str:
    """地址键 -> 安全的文件名（缓存用）。反斜杠、空格、& 都会出问题。"""
    cleaned = _SAFE.sub("_", key).strip("_")
    return cleaned or "k"
