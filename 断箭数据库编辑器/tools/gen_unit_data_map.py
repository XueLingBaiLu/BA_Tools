# ruff: noqa: E501, UP031
# 本文件里成段的中文是**版面内容**（会原样画到图上），按 100 列硬折会把文案切碎；
# 与 tools/gen_infantry_map.py 同一口径。其余规则照旧。
"""
生成《单位数据表 · 关系与改动影响图》。

产出：docs/单位数据表-关系与改动图.png

**数字全部现读** DataBaseCompiled/52085（52082 做对照）：行数、覆盖单位数、共享度
都从明文 JSON 算，不写死。共享度的口径与 src/ba_mod_maker/core/edit/sharing.py 一致
= 这一行被多少个单位引用（关联行天然是 1 = 独占）。

用法：
    .venv/Scripts/python.exe tools/gen_unit_data_map.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
DB = REPO.parent / "DataBaseCompiled"
OUT = REPO / "docs" / "单位数据表-关系与改动图.png"

sys.path.insert(0, str(REPO / "src"))
from ba_mod_maker.app.theme.tokens import TOKENS  # noqa: E402

C = TOKENS.color


def hexc(v: str) -> tuple[int, int, int]:
    v = v.lstrip("#")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


BG = hexc(C.bg_base)
PANEL = hexc(C.bg_panel)
ELEV = hexc(C.bg_elevated)
INPUT = hexc(C.bg_input)
BORDER = hexc(C.border)
BORDER2 = hexc(C.border_strong)
TX = hexc(C.text_primary)
TXS = hexc(C.text_strong)
TX2 = hexc(C.text_secondary)
TX3 = hexc(C.text_muted)
ACCENT = hexc(C.accent)
RED = hexc(C.danger)
BLUE = hexc(C.info)
ORANGE = hexc(C.cost_text)
ORANGE_BG = hexc(C.cost_bg)
GREEN = hexc(C.success)
WARN = hexc(C.warning)
DANGER_BG = hexc(C.danger_subtle)
INFO_BG = hexc(C.info_subtle)

FONT_DIR = Path(os.environ.get("LOCALAPPDATA") or "") / "BA_Mod_Maker" / "fonts"
WIN = Path(r"C:\Windows\Fonts")


def _pick(*paths: Path) -> str | None:
    for p in paths:
        if p.is_file():
            return str(p)
    return None


CJK = _pick(FONT_DIR / "NotoSerifCJKsc-Regular.ttf", WIN / "msyh.ttc", WIN / "simhei.ttf")
CJK_B = _pick(
    FONT_DIR / "NotoSerifCJKsc-SemiBold.ttf", WIN / "msyhbd.ttc", CJK or WIN / "simhei.ttf"
)
LAT = _pick(FONT_DIR / "Inter-Regular.ttf", WIN / "arial.ttf")
LAT_B = _pick(FONT_DIR / "Inter-SemiBold.ttf", WIN / "arialbd.ttf")

_fc: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(size: int, *, bold: bool = False, latin: bool = False) -> ImageFont.FreeTypeFont:
    key = ("L" if latin else "C") + ("b" if bold else "r") + str(size)
    if key in _fc:
        return _fc[key]
    path = (LAT_B if bold else LAT) if latin else (CJK_B if bold else CJK)
    if path is None:
        path = CJK_B if bold else CJK
    _fc[key] = ImageFont.truetype(str(path), size)
    return _fc[key]


# ---------------------------------------------------------------- 读数据 + 算指标
LINK_TABLES = (
    "Modifications",
    "UnitArmors",
    "UnitPropulsions",
    "SensorUnits",
    "UnitAbilities",
    "TurretUnits",
    "SquadMembers",
    "SquadWeapons",
    "WeaponAmmunitions",
    "SpecializationAvailabilities",
    "TransportAvailabilities",
)

#: 归属流：(父, 子, 形态, 列)。child_fk = 子上有指向父的列；parent_fk = 父上有指向子的列。
FLOW: list[tuple[str, str, str, str]] = [
    *[("Units", c, "child_fk", "UnitId") for c in LINK_TABLES],
    ("Units", "Countries", "parent_fk", "CountryId"),
    ("Modifications", "Options", "child_fk", "ModificationId"),
    ("UnitPropulsions", "Mobility", "parent_fk", "MobilityId"),
    ("UnitArmors", "Armors", "parent_fk", "ArmorId"),
    ("SensorUnits", "Sensors", "parent_fk", "SensorId"),
    ("UnitAbilities", "Abilities", "parent_fk", "AbilityId"),
    ("TurretUnits", "Turrets", "parent_fk", "TurretId"),
    ("Turrets", "Turrets", "parent_fk", "ParentTurretId"),
    ("Turrets", "TurretWeapons", "child_fk", "TurretId"),
    ("TurretWeapons", "Weapons", "parent_fk", "WeaponId"),
    ("SquadWeapons", "Weapons", "parent_fk", "WeaponId"),
    ("SquadMembers", "Weapons", "parent_fk", "PrimaryWeaponId"),
    ("SquadMembers", "Weapons", "parent_fk", "SpecialWeaponId"),
    ("WeaponAmmunitions", "Weapons", "parent_fk", "WeaponId"),
    ("WeaponAmmunitions", "Ammunitions", "parent_fk", "AmmunitionId"),
    ("Abilities", "Ammunitions", "parent_fk", "SmokeAmmunitionId"),
    ("Mobility", "PlaneFlyPresets", "parent_fk", "FlyPresetId"),
    ("SpecializationAvailabilities", "Specializations", "parent_fk", "SpecializationId"),
    (
        "SpecializationAvailabilities",
        "TransportAvailabilities",
        "child_fk",
        "SpecializationAvailabilityId",
    ),
    ("Options", "Armors", "parent_fk", "ArmorId"),
    ("Options", "Mobility", "parent_fk", "MobilityId"),
    ("Options", "Sensors", "parent_fk", "MainSensorId"),
    ("Options", "Sensors", "parent_fk", "ExtraSensorId"),
    ("Options", "Abilities", "parent_fk", "Ability1Id"),
    ("Options", "Abilities", "parent_fk", "Ability2Id"),
    ("Options", "Abilities", "parent_fk", "Ability3Id"),
    *[("Options", "Turrets", "parent_fk", "Turret%dId" % i) for i in range(21)],
]


def load(instance: str) -> dict[str, list[dict]]:
    root = DB / instance
    out: dict[str, list[dict]] = {}
    for fn in sorted(os.listdir(root)):
        if fn.endswith(".json") and not fn.startswith("_"):
            with open(root / fn, encoding="utf-8") as fh:
                out[fn[:-5]] = json.load(fh)
    return out


def analyse() -> dict:
    tables = load("52085")
    tables2 = load("52082")
    cols = {n: list(tables[n][0].keys()) for n in tables}
    vals = {n: [[r.get(c) for c in cols[n]] for r in tables[n]] for n in tables}
    ix = {n: cols[n].index("Id") for n in tables}

    units = {n: defaultdict(set) for n in tables}
    for v in vals["Units"]:
        units["Units"][v[ix["Units"]]] = {v[ix["Units"]]}

    for _ in range(12):
        changed = False
        for parent, child, kind, col in FLOW:
            if col not in cols[child if kind == "child_fk" else parent]:
                continue
            if kind == "child_fk":
                ci = cols[child].index(col)
                for v in vals[child]:
                    k = v[ci]
                    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
                        continue
                    src = units[parent].get(k)
                    if src and not src <= units[child][v[ix[child]]]:
                        units[child][v[ix[child]]] |= src
                        changed = True
            else:
                ci = cols[parent].index(col)
                for v in vals[parent]:
                    k = v[ci]
                    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
                        continue
                    src = units[parent].get(v[ix[parent]])
                    if src and not src <= units[child][k]:
                        units[child][k] |= src
                        changed = True
        if not changed:
            break

    # 共享度**用产品自己的实现**（core/edit/sharing.py）—— 唯一口径，别再写第二份。
    # 产品没覆盖的表（目前只有 Countries 这类没有引用边的）退回本图的直数。
    from ba_mod_maker.core.data.dataset import Instance as _Instance
    from ba_mod_maker.core.data.schema import load_manifest as _lm
    from ba_mod_maker.core.data.schema import load_schema as _ls
    from ba_mod_maker.core.edit.sharing import build_sharing_index as _bsi

    _root = DB
    _inst = _Instance(
        path_id="52085", directory=_root / "52085", schema=_ls(_root), manifest=_lm(_root / "52085")
    )
    prod = _bsi(_inst)

    type_of = {v[ix["Units"]]: v[cols["Units"].index("Type")] for v in vals["Units"]}
    type_labels = {2: "步兵", 4: "载具", 8: "直升机", 16: "飞机", 32: "舰船"}
    type_count: dict[str, int] = defaultdict(int)
    for ty in type_of.values():
        type_count[type_labels[ty]] += 1

    # 每单位行数（只有带 UnitId 的关联表才有意义）
    per_unit: dict[str, tuple[int, int, float]] = {}
    for n in LINK_TABLES:
        cnt: dict[int, int] = defaultdict(int)
        for v in vals[n]:
            u = v[cols[n].index("UnitId")]
            if isinstance(u, int) and u > 0:
                cnt[u] += 1
        if cnt:
            flat = sorted(cnt.values())
            per_unit[n] = (flat[0], flat[-1], statistics.mean(flat))

    # 每张表覆盖多少个单位（按单位类型拆）
    coverage_types: dict[str, dict[str, int]] = {}
    for n in tables:
        buckets: dict[str, int] = defaultdict(int)
        for us in units[n].values():
            for u in us:
                buckets[type_labels[type_of[u]]] += 1
        coverage_types[n] = dict(buckets)

    stats: dict[str, dict] = {}
    for n in tables:
        owned = units[n]
        cov = len(set().union(*owned.values())) if owned else 0
        # 共享度：优先用产品口径；它给全 0（这张表没有引用边）时退回本图直数
        prod_vals = [prod.of(n, v[ix[n]]) for v in vals[n]]
        vals_used = prod_vals if max(prod_vals, default=0) > 0 else [len(s) for s in owned.values()]
        shares = [v for v in vals_used if v > 0]
        stats[n] = {
            "rows": len(tables[n]),
            "rows2": len(tables2.get(n, [])),
            "covered": cov,
            "owned": len(shares),
            "mean": round(statistics.mean(shares), 2) if shares else 0,
            "mx": max(shares) if shares else 0,
            "multi": sum(1 for s in shares if s > 1),
            "median": statistics.median(shares) if shares else 0,
            "source": "sharing.py" if max(prod_vals, default=0) > 0 else "本图直数",
        }

    return {
        "stats": stats,
        "per_unit": per_unit,
        "type_count": dict(type_count),
        "coverage_types": coverage_types,
        "unit_rows": len(tables["Units"]),
        "unit_rows2": len(tables2["Units"]),
        "cols": {n: len(cols[n]) for n in tables},
    }


# ---------------------------------------------------------------- 版面常量
W = 3400
M = 40
BOX_W = 540
GAP = 14
TITLE_H = 150
PAD = 22


def wrap(draw: ImageDraw.ImageDraw, s: str, f: ImageFont.FreeTypeFont, maxw: int) -> list[str]:
    """按像素宽度折行（中文逐字、英文按空格优先）。"""
    # 把连续的拉丁词（含 _ . - 数字）当成一个不可拆的块 —— 免得 ExtraSensorId 被劈成两半
    tokens: list[str] = []
    buf = ""
    for ch in s:
        if ch == "\n":
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append("\n")
        elif ch.isascii() and (ch.isalnum() or ch in "_./-+"):
            buf += ch
        else:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
    if buf:
        tokens.append(buf)
    lines: list[str] = []
    cur = ""
    for tok in tokens:
        if tok == "\n":
            lines.append(cur)
            cur = ""
            continue
        if draw.textlength(cur + tok, font=f) <= maxw:
            cur += tok
        else:
            if cur:
                lines.append(cur)
            cur = tok
    if cur:
        lines.append(cur)
    return lines


def rrect(draw, box, r, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def txt(draw, xy, s, f, fill, anchor="la"):
    draw.text(xy, s, font=f, fill=fill, anchor=anchor)


# ---------------------------------------------------------------- 节点内容
#: 每张表的「改什么」一句话。这张表是给用户看的，不是给校验器看的。
WHAT = {
    "Units": "改名字 / 阵营 / 类型 / 成本 / 血上限 / 隐蔽 / 座位 / 尺寸重量 / 模型·立绘·缩略图·音效 / 进不进卡墙",
    "UnitPropulsions": "换或加一条机动 → 前进·倒车·越野速度、转向、重量全跟着变",
    "UnitArmors": "换装甲套装 → 血量与六面装甲（动能/破甲）一起变；至少一条 IsDefault=true",
    "SensorUnits": "换观瞄/雷达 → 卡片「视野」与雷达加成跟着变（卡片视野 = max(地面, 高空) × 2）",
    "UnitAbilities": "加/减能力 → ECM、APS、烟幕、诱饵、补给、激光指示、冲刺",
    "TurretUnits": "加一个炮塔/挂架，或换炮塔；Order = 装填顺序",
    "WeaponAmmunitions": "这门武器在这个单位上带什么弹、带几发（Quantity）—— 同一门枪在不同单位可以不同",
    "SquadMembers": "加人/减人、换每名士兵的枪与模型（一行 = 一名士兵）",
    "SquadWeapons": "步兵班的武器清单；必须包含 SquadMembers 用到的每一门枪（单向包含）",
    "Modifications": "这个单位有哪几个改装槽（装甲 / 武器 / 机动 …）—— 加槽或删槽",
    "SpecializationAvailabilities": "这个单位在哪个专精、哪一档 XP 可用，以及可用数量 → 决定卡组里能不能选",
    "TransportAvailabilities": "谁可以被谁运输（专精 × 单位）",
    "Armors": "血量 + 六面装甲（动能 / 破甲 / 顶 / 侧 / 后）—— 改一行会波及所有引用它的单位",
    "Mobility": "前进·倒车·越野速度、转向、加速度、重量、浮渡·空投、飞行包线",
    "Sensors": "地面 / 低空 / 高空光学与雷达 —— 卡片「视野」的原料",
    "Abilities": "ECM 精度、雷达修正、激光射程、冲刺、烟幕、APS 数量与冷却、诱饵、补给半径",
    "Turrets": "旋转范围 / 转速 / 模型 / 子炮塔（ParentTurretId 自引用，86 个子炮塔）",
    "Weapons": "瞄准时间、弹匣、装填、射速（点射发数与间隔）、图标、模型、音效",
    "Ammunitions": "伤害 / 穿深 / 射程 / 散布 / 速度 / 无视掩体 / 能否被 APS 拦截 / 能打什么目标",
    "Specializations": "师级专精的名字、图标、插画，以及**卡组的槽位与点数预算**",
    "Countries": "阵营（RUS / USA / Editor）—— 改的是「属于谁」",
    "Options": "改装件费用 Cost、名称、图标，以及**覆盖列**：装甲 / 机动 / 传感器 / 能力 / 炮塔 / 隐蔽 / 整单位替换",
    "TurretWeapons": "这个炮塔能装哪几门武器；WeaponChannel / WeaponPriority 按挂载点覆盖武器行同名值",
    "PlaneFlyPresets": "飞行包线：最大速度、角速度、爬升、加减速、俯仰滚转",
}

#: 一行 = 什么（24 张表一览用）
ONE_ROW = {
    "Units": "一个单位",
    "UnitPropulsions": "某单位的一条机动",
    "UnitArmors": "某单位的一套装甲",
    "SensorUnits": "某单位的一个传感器",
    "UnitAbilities": "某单位的一个能力",
    "TurretUnits": "某单位装的一个炮塔/挂架",
    "WeaponAmmunitions": "某单位某门武器的一个弹种+携弹量",
    "SquadMembers": "某步兵单位的一名士兵",
    "SquadWeapons": "某步兵单位的一门班用武器",
    "Modifications": "某单位的一个改装槽",
    "SpecializationAvailabilities": "某单位在某专精的可选档位",
    "TransportAvailabilities": "一条「谁运谁」配对",
    "Options": "某个改装槽里的一个选项",
    "TurretWeapons": "某个炮塔能装的一门武器",
    "Armors": "一套装甲模板",
    "Mobility": "一套机动模板",
    "Sensors": "一套观瞄模板",
    "Abilities": "一个能力模板",
    "Turrets": "一个炮塔模板",
    "Weapons": "一门武器模板",
    "Ammunitions": "一个弹药模板",
    "Specializations": "一个师级专精",
    "Countries": "一个阵营",
    "PlaneFlyPresets": "一套飞行包线",
}

BAND2 = [
    "UnitPropulsions",
    "UnitArmors",
    "SensorUnits",
    "UnitAbilities",
    "UnitAbilities",
    "TurretUnits",
]
BAND2 = [
    "UnitPropulsions",
    "UnitArmors",
    "SensorUnits",
    "UnitAbilities",
    "TurretUnits",
    "WeaponAmmunitions",
    "SquadMembers",
    "SquadWeapons",
    "Modifications",
    "SpecializationAvailabilities",
    "TransportAvailabilities",
]
BAND3 = [
    "Armors",
    "Mobility",
    "Sensors",
    "Abilities",
    "Turrets",
    "Weapons",
    "Ammunitions",
    "Specializations",
    "Countries",
    "PlaneFlyPresets",
]
BAND4 = ["Options", "TurretWeapons"]

#: 关联行的适用范围备注
SCOPE = {
    "UnitPropulsions": "必需 · 540/540 都有",
    "UnitArmors": "必需 · 且 ≥1 条 IsDefault=true",
    "SensorUnits": "539/540 有",
    "UnitAbilities": "453/540 有",
    "TurretUnits": "载具 / 飞机 / 直升机",
    "WeaponAmmunitions": "505/540 有",
    "SquadMembers": "仅步兵（194）",
    "SquadWeapons": "仅步兵（194）",
    "Modifications": "351/540 有",
    "SpecializationAvailabilities": "338/540 有",
    "TransportAvailabilities": "140/540 有",
}

#: 共享模板的「入口」——谁通过哪一列引用它
ENTRY = {
    "Armors": "UnitArmors.ArmorId · Options.ArmorId",
    "Mobility": "UnitPropulsions.MobilityId · Options.MobilityId",
    "Sensors": "SensorUnits.SensorId · Options.MainSensorId / ExtraSensorId",
    "Abilities": "UnitAbilities.AbilityId · Options.Ability1..3Id",
    "Turrets": "TurretUnits.TurretId · Options.Turret0..20Id · Turrets.ParentTurretId",
    "Weapons": "TurretWeapons.WeaponId · SquadWeapons.WeaponId · SquadMembers.Primary/SpecialWeaponId · WeaponAmmunitions.WeaponId",
    "Ammunitions": "WeaponAmmunitions.AmmunitionId · Abilities.SmokeAmmunitionId",
    "Specializations": "SpecializationAvailabilities.SpecializationId",
    "Countries": "Units.CountryId · Specializations.CountryId",
    "PlaneFlyPresets": "Mobility.FlyPresetId",
    "Options": "Modifications.ModificationId",
    "TurretWeapons": "Turrets.TurretId（挂在炮塔上，不挂单位）",
}

#: 24 表一览用的短句（比 WHAT 短，塞得进表格一列）
WHAT_SHORT = {
    "Units": "名字·阵营·类型·成本·血量·隐蔽·座位·资源",
    "UnitPropulsions": "这个单位能跑多快（指向哪几条机动）",
    "UnitArmors": "这个单位用哪几套装甲",
    "SensorUnits": "这个单位有哪些观瞄/雷达",
    "UnitAbilities": "这个单位有哪些能力",
    "TurretUnits": "装了哪些炮塔/挂架，Order=顺序",
    "WeaponAmmunitions": "这门武器带什么弹、带几发",
    "SquadMembers": "一个班几个人、每人什么枪",
    "SquadWeapons": "班用武器清单（必须含成员用到的枪）",
    "Modifications": "有哪几个改装槽",
    "SpecializationAvailabilities": "在哪个专精哪一档可选、数量",
    "TransportAvailabilities": "谁可以被谁运输",
    "Options": "改装件费用 + 覆盖维度（换装甲/机动/炮塔…）",
    "TurretWeapons": "这个炮塔能装哪几门武器",
    "Armors": "血量 + 六面装甲（动能/破甲）",
    "Mobility": "前进/倒车/越野速度、转向、重量",
    "Sensors": "地面/低空/高空光学与雷达",
    "Abilities": "ECM/雷达/激光/冲刺/烟幕/APS/诱饵/补给",
    "Turrets": "旋转范围、转速、模型、子炮塔",
    "Weapons": "瞄准时间、弹匣、装填、射速、图标",
    "Ammunitions": "伤害/穿深/射程/散布/速度/无视掩体",
    "Specializations": "专精名字·图标 + 卡组槽位与点数预算",
    "Countries": "阵营：RUS / USA / Editor",
    "PlaneFlyPresets": "飞行包线：速度、角速度、爬升",
}

#: 单位类型 → 需要哪些子树
TYPE_TREE = [
    (
        "步兵 Type=2",
        "194 个",
        "SquadMembers + SquadWeapons + UnitPropulsions + UnitArmors + 其余通用",
    ),
    (
        "载具 Type=4",
        "234 个",
        "TurretUnits + TurretWeapons + WeaponAmmunitions + UnitPropulsions + UnitArmors",
    ),
    ("直升机 Type=8", "45 个", "同上（炮塔即挂架）"),
    ("飞机 Type=16", "62 个", "同上 + Mobility.FlyPresetId → PlaneFlyPresets"),
    (
        "舰船 Type=32",
        "5 个",
        "同上；速度可能读 Mobility.MaxSpeedWater（**强假设**，等一张截图定案）",
    ),
]

READ_POINTS = [
    (
        "一行 ≠ 一个单位",
        "24 张表里只有 Units 是「单位表」——一行就是一个单位。其余 23 张要么"
        "「属于某一个单位」，要么「被很多个单位共用」。",
        RED,
    ),
    (
        "三种行，颜色就是语义",
        "红 = 单位行（540 行）｜蓝 = 独占关联行（带 UnitId，改它只动本单位）｜"
        "橙 = 共享模板行（改它会动一片，必须先「另存为新模板」）。",
        ORANGE,
    ),
    (
        "共享度是实测的，不是估的",
        "共享度最高的五张表：{top}（单位：这一行被多少个单位共用）。"
        "数字就印在每个模板框里，口径 = 产品自己的 core/edit/sharing.py。",
        BLUE,
    ),
    (
        "0 = 不使用，不是「指向 Id 0」",
        "所有 *Id 外键列上，0 都表示「这一项不装」。另外 Units.OriginalName / OriginalCost / "
        "Description 三列全表同值（占位），别当依据。",
        GREEN,
    ),
    (
        "两份库都要改",
        "52085 = 非战役（遭遇战/联机/编辑器）｜52082 = 战役。两库行数不同"
        "（540 vs 478），只改一份 = 只有一半模式生效。",
        WARN,
    ),
]


def node_height(d, width, title, body, *, title_size=27, body_size=18) -> int:
    """算一个节点真正需要多高（按折行后的实际行数）。"""
    h = 12 + title_size + 10
    for line in body:
        text, _color, size = line[0], line[1], line[2]
        h += (size + 7) * len(wrap(d, text, font(size), width - 34))
    return h + 26


def draw_node(d, box, title, badge, body, accent, *, title_size=27, body_size=18):
    x0, y0, x1, y1 = box
    rrect(d, box, 9, fill=ELEV, outline=accent, width=2)
    d.rectangle((x0 + 1, y0 + 1, x0 + 6, y1 - 1), fill=accent)
    txt(d, (x0 + 18, y0 + 12), title, font(title_size, latin=True, bold=True), TXS)
    if badge:
        bw = d.textlength(badge, font=font(body_size - 2))
        txt(d, (x1 - 16, y0 + 16), badge, font(body_size - 2), accent, anchor="ra")
        del bw
    yy = y0 + 12 + title_size + 8
    for line in body:
        text, color, size, bold = (line + (False,))[:4] if len(line) == 3 else line
        f = font(size, bold=bold)
        for piece in wrap(d, text, f, (x1 - x0) - 34):
            txt(d, (x0 + 18, yy), piece, f, color)
            yy += size + 6
    return yy


def render() -> Image.Image:
    analysis = analyse()
    stats = analysis["stats"]
    top = "、".join(
        "%s %d" % (name, stats[name]["mx"])
        for name in sorted(stats, key=lambda k: -stats[k]["mx"])[:5]
    )
    read_points = [
        (head, body.format(top=top) if "{top}" in body else body, color)
        for head, body, color in READ_POINTS
    ]
    img = Image.new("RGB", (W, 4800), BG)
    d = ImageDraw.Draw(img)

    def metric_row(d, box, items, accent):
        x0, y0, x1, _ = box
        x = x0 + 18
        y = y0
        for label, value in items:
            f1 = font(15)
            f2 = font(24, latin=True, bold=True)
            txt(d, (x, y), label, f1, TX3)
            txt(d, (x, y + 18), value, f2, accent)
            x += max(d.textlength(label, font=f1), d.textlength(value, font=f2)) + 26

    # ------------------------------------------------ 标题
    y = 34
    txt(d, (M, y), "单位数据表 · 关系与改动影响图", font(60, bold=True), TXS)
    y += 74
    txt(
        d,
        (M, y),
        "DataBaseCompiled 24 张表 ｜ 实例 52085（非战役，Units 540 行 / 31 列）+ 52082 对照"
        " ｜ 全部读数本轮实测，可复跑：tools/gen_unit_data_map.py",
        font(21),
        TX2,
    )
    y += 40
    d.line((M, y, W - M, y), fill=BORDER2, width=2)
    y += 22

    # ------------------------------------------------ 读图要点
    txt(d, (M, y), "读图要点", font(26, bold=True), TX2)
    y += 40
    rp_h = 168
    rp_w = (W - 2 * M - 4 * GAP) // 5
    for i, (head, body, color) in enumerate(read_points):
        x = M + i * (rp_w + GAP)
        box = (x, y, x + rp_w, y + rp_h)
        rrect(d, box, 8, fill=PANEL, outline=color, width=2)
        d.rectangle((x + 1, y + 1, x + 5, y + rp_h - 1), fill=color)
        txt(d, (x + 18, y + 16), head, font(24, bold=True), color)
        yy = y + 54
        for piece in wrap(d, body, font(18), rp_w - 36):
            txt(d, (x + 18, yy), piece, font(18), TX)
            yy += 26
    y += rp_h + 34

    # ------------------------------------------------ 图例
    legend = [
        ("单位行", RED, "一行 = 一个单位，改它只动一个单位"),
        ("独占关联行", BLUE, "带 UnitId，一行属于一个单位，改它只动本单位"),
        ("共享模板行", ORANGE, "一行被 N 个单位共用，改前要「另存为新模板」"),
    ]
    x = M
    for name, color, note in legend:
        rrect(d, (x, y, x + 30, y + 30), 6, fill=color)
        txt(d, (x + 42, y + 2), name, font(24, bold=True), color)
        wname = d.textlength(name, font=font(24, bold=True))
        txt(d, (x + 42 + wname + 14, y + 6), note, font(19), TX2)
        x += 42 + wname + 14 + d.textlength(note, font=font(19)) + 60
    y += 52

    # ------------------------------------------------ 带 1：Units
    ub = (M, y, W - M, y + 214)
    rrect(d, ub, 10, fill=hexc(C.bg_selected), outline=RED, width=3)
    d.rectangle((ub[0] + 1, ub[1] + 1, ub[0] + 7, ub[3] - 1), fill=RED)
    txt(d, (ub[0] + 24, ub[1] + 16), "Units", font(44, latin=True, bold=True), RED)
    txt(
        d,
        (ub[0] + 200, ub[1] + 30),
        "单位表 —— 540 行 = 540 个单位（战役库 52082 是 478 行）",
        font(24, bold=True),
        TX,
    )
    rows = [
        (
            "身份 8 列",
            "Id · Name · HUDName · CountryId → Countries · Type · CategoryType · Role · DisplayInArmory",
        ),
        (
            "成本 4 列",
            "Cost（基础价）· UpkeepMultiplier · TimeModifierRefund · TimeModifierResurrect",
        ),
        (
            "生存 5 列",
            "MaxStress（血上限）· Stealth · CurrentStealth · InfantrySlots · WaterDiveOffset",
        ),
        ("尺寸重量 4 列", "Length · Width · Height · Weight（步兵重量 = 125 × 人数，要自己算）"),
        (
            "资源 4 列",
            "ModelFileName · PortraitFileName · ThumbnailFileName · AudioPreset（都是 catalog 地址键）",
        ),
    ]
    yy = ub[1] + 82
    for i, (k, v) in enumerate(rows):
        col = i % 2
        row = i // 2
        xx = ub[0] + 24 + col * 1640
        yyy = yy + row * 32
        txt(d, (xx, yyy), k, font(19, bold=True), TX2)
        txt(d, (xx + 190, yyy), v, font(19), TX)
    txt(
        d,
        (ub[0] + 24, ub[3] - 36),
        "还有 3 列是占位（OriginalName · OriginalCost · Description —— 全表同值）；"
        "另外 IsUnitModification / ContentMembership 各 78 / 540 行非默认。",
        font(18),
        TX3,
    )
    y = ub[3] + 40

    # 带间箭头
    def draw_band(d, y, names, per_row, accent, body_of, badge_of, *, title_size=25):
        """一排节点，行高按本行最高的那个节点自适应。"""
        bodies = {n: body_of(n) for n in names}
        heights = {n: node_height(d, BOX_W, n, bodies[n], title_size=title_size) for n in names}
        row_h: dict[int, int] = {}
        for i, n in enumerate(names):
            r = i // per_row
            row_h[r] = max(row_h.get(r, 0), heights[n])
        for i, n in enumerate(names):
            col, r = i % per_row, i // per_row
            x = M + col * (BOX_W + GAP)
            yy = y + sum(row_h[k] + GAP for k in range(r))
            box = (x, yy, x + BOX_W, yy + row_h[r])
            draw_node(d, box, n, badge_of(n), bodies[n], accent, title_size=title_size)
        return y + sum(row_h.values()) + GAP * len(row_h) + 20

    def band_arrow(d, y, label, sub, color):
        cx = W // 2
        d.line((cx, y, cx, y + 34), fill=color, width=3)
        d.polygon([(cx - 9, y + 30), (cx + 9, y + 30), (cx, y + 44)], fill=color)
        f = font(22, bold=True)
        tw = d.textlength(label, font=f)
        txt(d, (cx + 18, y + 4), label, f, color)
        if sub:
            txt(d, (cx + 18 + tw + 16, y + 8), sub, font(19), TX2)
        return y + 58

    y = band_arrow(
        d,
        y,
        "① 每个单位各挂一批「自己的行」",
        "11 张关联表都带 UnitId：一行 = 这个单位的一项配置。改它只影响这一个单位。",
        BLUE,
    )

    # ------------------------------------------------ 带 2：11 张关联行
    def band2_body(name):
        st = stats[name]
        lo, hi, avg = analysis["per_unit"].get(name, (0, 0, 0))
        return [
            (f"{st['rows']} 行（52082：{st['rows2']}）· 覆盖 {st['covered']}/540 个单位", TX2, 18),
            (f"每单位 {lo}..{hi} 行（均 {avg:.2f}）｜{SCOPE.get(name, '')}", TX3, 17),
            (WHAT[name], TX, 18),
        ]

    def band2_badge(name):
        st = stats[name]
        return "独占" if st["mx"] <= 1 else f"均 {st['mean']}"

    y = draw_band(d, y, BAND2, 6, BLUE, band2_body, band2_badge, title_size=25)

    y = band_arrow(
        d,
        y,
        "② 关联行指向「共享模板」",
        "模板行不属于任何单位：一行被 N 个单位共用。改之前先看框里的「最大共享」。",
        ORANGE,
    )

    # ------------------------------------------------ 带 3：共享模板
    def band3_body(name):
        st = stats[name]
        return [
            (f"{st['rows']} 行（52082：{st['rows2']}）· 覆盖 {st['covered']}/540 个单位", TX2, 18),
            (f"均共享 {st['mean']} · {st['multi']} 行被不止一个单位用", TX3, 17),
            (f"入口：{ENTRY[name]}", TX3, 17),
            (WHAT[name], TX, 18),
        ]

    y = draw_band(
        d, y, BAND3, 5, ORANGE, band3_body, lambda n: f"最大共享 {stats[n]['mx']}", title_size=25
    )

    y = band_arrow(
        d,
        y,
        "③ 二级明细：改装与「炮塔能装什么」",
        "Options 是「换装改变单位」的唯一入口；TurretWeapons 挂在炮塔上、不挂单位。",
        GREEN,
    )

    b4w = (W - 2 * M - GAP) // 2
    b4_bodies = {}
    for name in BAND4:
        st = stats[name]
        if name == "Options":
            body = [
                (
                    f"{st['rows']} 行（52082：{st['rows2']}）· 覆盖 {st['covered']}/540 个单位 · "
                    "一行 = 某个改装槽里的一个选项",
                    TX2,
                    18,
                ),
                ("入口：Modifications.ModificationId（一个槽 2..11 个选项）", TX3, 17),
                (
                    "Cost（改装件费用）：卡片成本 = Units.Cost + 所有 IsDefault 选项的 Cost 之和",
                    TX,
                    18,
                ),
                (
                    "覆盖列：ArmorId 317 · MobilityId 297 · MainSensorId 184 · ExtraSensorId 1 · "
                    "Ability1/2/3Id 289/142/33 · StealthOverride 20 · Turret0..19Id 大量",
                    TX,
                    18,
                ),
                (
                    "还有 ReplaceUnitId 143（整单位替换 = 变体）· ThumbnailOverride 224 · PortraitOverride 23 · "
                    "TimeModifierRefund 12 · Resurrect 7",
                    TX,
                    18,
                ),
            ]
        else:
            body = [
                (
                    f"{st['rows']} 行（52082：{st['rows2']}）· 覆盖 {st['covered']}/540 个单位 · "
                    f"均共享 {st['mean']} · 最大 {st['mx']}",
                    TX2,
                    18,
                ),
                (f"入口：{ENTRY[name]}", TX3, 17),
                (WHAT[name], TX, 18),
                (
                    "⚠ 它自己不带 UnitId：单位归属要经 TurretUnits 绕一跳（core/edit/sharing.py 的 Edge.via）",
                    TX3,
                    17,
                ),
            ]
        b4_bodies[name] = body
    b4h = max(node_height(d, b4w, n, b4_bodies[n], title_size=25) for n in BAND4)
    for idx, name in enumerate(BAND4):
        x = M + idx * (b4w + GAP)
        box = (x, y, x + b4w, y + b4h)
        st = stats[name]
        draw_node(
            d,
            box,
            name,
            f"最大共享 {st['mx']}" if st["mx"] else "",
            b4_bodies[name],
            GREEN,
            title_size=25,
        )
    y += b4h + 26

    # 改装覆盖：橙色虚线，从 Options 回到带 3
    d.line((M + 4, y - 40, M + 4, y - 40), fill=ORANGE)
    txt(
        d,
        (M, y),
        "橙色虚线关系（覆盖）：Options 的 8 类覆盖列把「装甲 / 机动 / 传感器 / 能力 / 炮塔 / 隐蔽」"
        "整套换成另一行 —— 换装后卡片必须跟着重算（已实测：成本 / 武器 / 数值 / 名字 / 缩略图 / 装甲 六条链）。",
        font(19),
        ORANGE,
    )

    # ------------------------------------------------ 单位类型 → 子树
    y += 40
    txt(d, (M, y), "Type 决定需要哪些子树（540 个单位的构成）", font(28, bold=True), TX2)
    y += 46
    th = 44
    for i, (name, cnt, tree) in enumerate(TYPE_TREE):
        yy = y + i * th
        if i % 2 == 0:
            d.rectangle((M, yy, W - M, yy + th - 4), fill=PANEL)
        txt(d, (M + 16, yy + 8), name, font(21, bold=True), RED)
        txt(d, (M + 250, yy + 8), cnt, font(21), TX2)
        txt(d, (M + 360, yy + 8), tree, font(21), TX)
    y += len(TYPE_TREE) * th + 30

    # ------------------------------------------------ 24 张表一览
    txt(d, (M, y), "24 张表一览：一行 = 什么，改它 = 改什么", font(28, bold=True), TX2)
    y += 46
    per_col = 8
    tw_gap = 26
    tbl_w = (W - 2 * M - 2 * tw_gap) // 3
    col_w = (
        252,
        176,
        96,
    )  # 表名 / 一行=什么 / 行数 —— 表名最长的 SpecializationAvailabilities 要放得下
    rest_w = tbl_w - 20 - sum(col_w)
    head_h = 40
    row_h = 46
    names = sorted(ONE_ROW)
    for group in range(3):
        gx = M + group * (tbl_w + 26)
        chunk = names[group * per_col : (group + 1) * per_col]
        d.rectangle((gx, y, gx + tbl_w, y + head_h), fill=ELEV)
        hx = gx + 10
        for label, cw in zip(("表", "一行 = 什么", "行数"), col_w, strict=False):
            txt(d, (hx, y + 10), label, font(19, bold=True), TX2)
            hx += cw
        txt(d, (hx, y + 10), "改它 = 改什么", font(19, bold=True), TX2)
        for r, name in enumerate(chunk):
            yy = y + head_h + r * row_h
            if r % 2 == 0:
                d.rectangle((gx, yy, gx + tbl_w, yy + row_h), fill=PANEL)
            hx = gx + 10
            color = RED if name == "Units" else (ORANGE if name in BAND3 else BLUE)
            txt(d, (hx, yy + 12), name, font(17, latin=True, bold=True), color)
            hx += col_w[0]
            txt(d, (hx, yy + 12), wrap(d, ONE_ROW[name], font(17), col_w[1] - 12)[0], font(17), TX)
            hx += col_w[1]
            st = stats[name]
            txt(d, (hx, yy + 12), f"{st['rows']}/{st['rows2']}", font(17, latin=True), TX2)
            hx += col_w[2]
            txt(d, (hx, yy + 12), wrap(d, WHAT_SHORT[name], font(17), rest_w)[0], font(17), TX)
    y += head_h + per_col * row_h + 30

    txt(
        d,
        (M, y),
        "口径：共享度 = 这一行被多少个单位引用（关联行天然为 1 = 独占），直接调 "
        "src/ba_mod_maker/core/edit/sharing.py::build_sharing_index —— 与界面上的角标同一个数，"
        "不再是第二份实现（Specializations / Countries / PlaneFlyPresets 这三张产品口径没有引用边，退回本图直数）。"
        "行数 / 覆盖单位 / 每单位行数本轮实测。数据源只读：DataBaseCompiled/52085 + 52082。",
        font(18),
        TX3,
    )
    y += 40

    # 真实内容高度：从底部往上找第一条非底色像素行（比猜 y 稳）
    px = img.load()
    bottom = y
    for yy in range(min(y, img.height) - 1, 0, -1):
        row_hit = False
        for xx in range(0, W, 7):
            if px[xx, yy] != BG:
                row_hit = True
                break
        if row_hit:
            bottom = yy + 30
            break
    return img.crop((0, 0, W, min(bottom, img.height)))


def main() -> int:
    img = render()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG")
    print(f"写出 {OUT}  {img.width}x{img.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
