# ruff: noqa: E501, UP031
# 本文件里成段的中文是**版面内容**（会原样画到图上），按 100 列硬折会把文案切碎；
# 与 tools/gen_infantry_map.py 同一口径。其余规则照旧。
"""生成《设计稿 v3 · 双击军械库上的单位 → 单位编辑器》的界面示意图。

产出：
    docs/设计稿-单位编辑器.png        图 A · 总览（军械库 + 单位编辑器 + 四条细节）
    docs/设计稿-专用页.png            图 B · 四个专用页（步兵班 / 武器 / 弹药 / Options）
    docs/设计稿-步兵班编辑器.png       单件
    docs/设计稿-武器编辑器.png         单件
    docs/设计稿-弹药编辑器.png         单件
    docs/设计稿-Options编辑器.png      单件

**图里的数字全部是真的**：卡片数据由产品自己的
app/viewmodel/unit_card.py::build_unit_card() 从 DataBaseCompiled/52085 现装配；
字段值直接读那一行；图标/立绘来自游戏包缓存。词条的中文名与注释优先取队友产出的
_work/field_labels.json 与 _work/field_enums.json（task-1 / task-2），没有就用本文件里的兜底表。

v3 按用户第二批口径改的六条（见 docs/设计-单位编辑器-双击进入.md §1）：
  1. 步兵班：点 +1 加人、点 ❌ 减人
  2. Options 也开专用页
  3. 跟单位相关的**所有提示都贴在立绘上**
  4. 外键**按 Name / HUDName 输入**，不让人查 Id
  5. 枚举 / 掩码显示成中文（制导类型、可瞄准目标…）
  6. 所有词条中文化 + 悬浮注释解释作用

用法：
    .venv/Scripts/python.exe tools/gen_unit_editor_mock.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from ba_mod_maker.app.theme.tokens import TOKENS  # noqa: E402
from ba_mod_maker.app.viewmodel.unit_card import CardData, build_unit_card  # noqa: E402
from ba_mod_maker.core.data.dataset import Instance  # noqa: E402
from ba_mod_maker.core.data.schema import load_manifest, load_schema  # noqa: E402
from ba_mod_maker.core.edit.foreign import display_name as core_display_name  # noqa: E402
from ba_mod_maker.core.edit.scope import unit_scope  # noqa: E402

C = TOKENS.color
DB = REPO.parent / "DataBaseCompiled"
ICONS = Path(os.environ.get("LOCALAPPDATA") or "") / "BA_Mod_Maker" / "icons"
DOCS = REPO / "docs"


def hexc(v: str):
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
PURPLE = (199, 146, 234)
SELECTED = hexc(C.bg_selected)
CARD_BG = hexc(C.card_bg)
CARD_SEL = hexc(C.card_bg_selected)
CARD_SEL_BORDER = hexc(C.card_border_selected)
SIL = hexc(C.silhouette)
SIL_SEL = hexc(C.silhouette_selected)
INFO_BG = hexc(C.info_subtle)
DANGER_BG = hexc(C.danger_subtle)
SUCCESS_BG = hexc(C.success_subtle)

FONT_DIR = Path(os.environ.get("LOCALAPPDATA") or "") / "BA_Mod_Maker" / "fonts"
WIN = Path(r"C:\Windows\Fonts")


def _pick(*paths):
    for p in paths:
        if p and Path(p).is_file():
            return str(p)
    return None


CJK = _pick(FONT_DIR / "NotoSerifCJKsc-Regular.ttf", WIN / "msyh.ttc", WIN / "simhei.ttf")
CJK_B = _pick(FONT_DIR / "NotoSerifCJKsc-SemiBold.ttf", WIN / "msyhbd.ttc", CJK)
LAT = _pick(FONT_DIR / "Inter-Regular.ttf", WIN / "arial.ttf", CJK)
LAT_B = _pick(FONT_DIR / "Inter-SemiBold.ttf", WIN / "arialbd.ttf", CJK)

_fc: dict = {}


def F(size: int, *, bold: bool = False, latin: bool = False):
    key = ("L" if latin else "C") + ("b" if bold else "r") + str(size)
    if key not in _fc:
        path = (LAT_B if bold else LAT) if latin else (CJK_B if bold else CJK)
        _fc[key] = ImageFont.truetype(str(path), size)
    return _fc[key]


def wrap(d, s, f, maxw):
    toks, buf = [], ""
    for ch in s:
        if ch == "\n":
            if buf:
                toks.append(buf)
                buf = ""
            toks.append("\n")
        elif ch.isascii() and (ch.isalnum() or ch in "_./-+:"):
            buf += ch
        else:
            if buf:
                toks.append(buf)
                buf = ""
            toks.append(ch)
    if buf:
        toks.append(buf)
    lines, cur = [], ""
    for tk in toks:
        if tk == "\n":
            lines.append(cur)
            cur = ""
            continue
        if d.textlength(cur + tk, font=f) <= maxw:
            cur += tk
        else:
            if cur:
                lines.append(cur)
            cur = tk
    if cur:
        lines.append(cur)
    return lines


def rr(d, box, r, fill=None, outline=None, width=1):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def t(d, xy, s, f, fill, anchor="la"):
    d.text(xy, s, font=f, fill=fill, anchor=anchor)


def cut(d, s, f, maxw):
    out = s
    while out and d.textlength(out, font=f) > maxw:
        out = out[:-2]
    return out if out == s else (out[:-1] + "…" if out else out)


_icon_cache: dict = {}


def icon(family: str, key: str):
    ck = (family, key)
    if ck in _icon_cache:
        return _icon_cache[ck]
    result = None
    folder = ICONS / family
    if folder.is_dir() and key:
        sani = key.replace("\\", "_").replace("/", "_").replace(" ", "_")
        stem = sani[:-4] if sani.lower().endswith(".png") else sani
        cands = [folder / sani]
        cands += [folder / (sani + e) for e in (".png", ".jpg", ".jpeg")]
        cands += [folder / (stem + e) for e in (".png", ".jpg", ".jpeg")]
        for cand in cands:
            if cand.is_file():
                result = Image.open(cand).convert("RGBA")
                break
        if result is None:
            base = key.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
            hits = [p for p in folder.glob("*") if p.stem in (base, base + ".png")]
            if hits:
                result = Image.open(hits[0]).convert("RGBA")
    _icon_cache[ck] = result
    return result


def paste_fit(canvas, im, box, *, tint=None):
    x0, y0, x1, y1 = box
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)
    src = im
    if tint is not None:
        src = Image.new("RGBA", im.size, tuple(tint) + (255,))
        src.putalpha(im.getchannel("A"))
    scale = min(bw / src.width, bh / src.height)
    nw, nh = max(1, int(src.width * scale)), max(1, int(src.height * scale))
    canvas.alpha_composite(
        src.resize((nw, nh), Image.LANCZOS), (x0 + (bw - nw) // 2, y0 + (bh - nh) // 2)
    )


# ---------------------------------------------------------------- 词条词典
#: 字段词典的落点。**产品自带那一份优先** —— 与 core/rules/rulebook.json 同一口径：
#: 产品知识随产品走；用户直接打开 BA_Mod.zip 时，旁边不会有 _work 目录。
#: _work 那份是队友生成时的落点，留作兜底。
FIELD_DIRS = (REPO / "src" / "ba_mod_maker" / "core" / "fields", WORK)

LABELS: dict = {}
ENUMS: dict = {}


def _read_json(name: str) -> dict:
    for folder in FIELD_DIRS:
        path = folder / name
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
    return {}


def load_dicts() -> tuple[int, int]:
    global LABELS, ENUMS
    LABELS = _read_json("field_labels.json")
    ENUMS = _read_json("field_enums.json")
    return len(LABELS), len(ENUMS)


#: 兜底（只覆盖图里画到的那些；队友的全量表到了会自动盖过去）
FALLBACK: dict[str, tuple[str, str]] = {
    "Units.Id": ("单位 Id", "这个单位在 Units 表里的唯一键。全库 540 行，Id 不连续是常态"),
    "Units.Name": ("内部名", "开发用的名字。卡片上显示的是 HUDName"),
    "Units.HUDName": ("显示名", "卡片抬头写的就是它；选项里的 ReplaceUnitName 会盖掉它"),
    "Units.CountryId": ("阵营", "→ Countries。RUS / USA / Editor"),
    "Units.Type": ("单位类型", "2=步兵 4=载具 8=直升机 16=飞机 32=舰船。它决定需要哪些子树"),
    "Units.CategoryType": (
        "军械库分类",
        "卡墙左边那一列的分类标签（侦察/步兵/载具/支援/未分类/直升机/战机）",
    ),
    "Units.Role": ("角色", "侦察/支援之类的小标签，卡片副标题里那个"),
    "Units.DisplayInArmory": ("进不进卡墙", "false = 不进军械库卡片墙（例如 LHA-6 那几条船）"),
    "Units.IsUnitModification": ("是变体单位", "true = 由某个选项 ReplaceUnitId 指过来的变体"),
    "Units.ContentMembership": ("内容归属", "DLC / 本体归属位；−1 = 不限"),
    "Units.Cost": (
        "基础成本",
        "卡片上那个金色数字的基数。实际价 = 本列 + 所有 IsDefault 选项的 Cost 之和",
    ),
    "Units.UpkeepMultiplier": ("维护倍率", "全表恒 1.0（占位）"),
    "Units.TimeModifierRefund": ("撤回时间修正", "秒。毫发无损撤回那一路会加上它"),
    "Units.TimeModifierResurrect": (
        "复活时间修正",
        "秒。阵亡后整备时间 = 1.5×Cost + 420 + 本列；BTR-82A 是 −120 → 卡片 6:22",
    ),
    "Units.MaxStress": (
        "最大压力值",
        "卡片「生命值」那一格的原料；实际显示要经过装甲套装的 MaxHealthPoints",
    ),
    "Units.Stealth": ("隐蔽（原始）", "基础隐蔽；卡片读的是 CurrentStealth"),
    "Units.CurrentStealth": (
        "隐蔽（当前）",
        "卡片「隐身」= 1 / 本列。选项的 StealthOverride 会盖掉它",
    ),
    "Units.InfantrySlots": (
        "座位数",
        "能装几个步兵。0 = 卡片上不画这一格；卡片读的是 GetInfantrySlots()",
    ),
    "Units.WaterDiveOffset": ("入水深度偏移", "两栖/潜渡时模型下沉多少"),
    "Units.Length": ("长", "米。只影响显示与碰撞尺寸"),
    "Units.Width": ("宽", "米"),
    "Units.Height": ("高", "米"),
    "Units.Weight": (
        "重量",
        "kg。❗步兵这一列恒为 1，卡片上的重量是 125 × 人数（LoadUnits 里算的）",
    ),
    "Units.ModelFileName": ("模型键", "→ catalog 地址键，指向 3D 预制体"),
    "Units.PortraitFileName": ("立绘键", "→ catalog 地址键。选项的 PortraitOverride 会盖掉它"),
    "Units.ThumbnailFileName": (
        "缩略图键",
        "→ catalog 地址键，卡片墙上那张图。选项的 ThumbnailOverride 会盖掉它",
    ),
    "Units.AudioPreset": ("音效预设", "选中/移动时的语音与引擎声"),
    "Units.OriginalName": ("原版名", "❗全表同值（占位），别当依据"),
    "Units.OriginalCost": ("原版成本", "❗全表 0（占位）。基础价是 Cost"),
    "Units.Description": ("描述", "❗全表同值（占位）"),
    "Units.OwnerInfantryID": ("所属步兵 Id", "❗全表 0，从未被写入"),
    "UnitPropulsions.UnitId": ("属于哪个单位", "→ Units"),
    "UnitPropulsions.MobilityId": ("机动模板", "→ Mobility。改它 = 换一套速度/转向/重量"),
    "UnitArmors.UnitId": ("属于哪个单位", "→ Units"),
    "UnitArmors.ArmorId": ("装甲套装", "→ Armors。改它 = 血量与六面装甲一起变"),
    "SensorUnits.UnitId": ("属于哪个单位", "→ Units"),
    "SensorUnits.SensorId": ("传感器", "→ Sensors。改它 = 换视野/雷达"),
    "UnitAbilities.UnitId": ("属于哪个单位", "→ Units"),
    "UnitAbilities.AbilityId": ("能力", "→ Abilities。ECM / APS / 烟幕 / 诱饵 / 补给 / 激光"),
    "TurretUnits.UnitId": ("属于哪个单位", "→ Units"),
    "TurretUnits.TurretId": ("炮塔 / 挂架", "→ Turrets。飞机的挂架也是炮塔"),
    "TurretUnits.Order": ("装填顺序", "1519/1597 是 0；非 0 的多是右炮塔/车体炮塔/指挥塔"),
    "WeaponAmmunitions.UnitId": ("属于哪个单位", "→ Units。这门武器在这个单位上的配置"),
    "WeaponAmmunitions.WeaponId": ("武器", "→ Weapons"),
    "WeaponAmmunitions.AmmunitionId": ("弹种", "→ Ammunitions"),
    "WeaponAmmunitions.Quantity": (
        "携弹量",
        "发数。同一门枪在不同单位可以不一样（Mk19：LVSR 240 / M1150 640）",
    ),
    "WeaponAmmunitions.Order": ("顺序", "弹种在卡片上的排列顺序"),
    "Modifications.UnitId": ("属于哪个单位", "→ Units"),
    "Modifications.Name": ("槽位名", "内部名"),
    "Modifications.Type": ("槽位类型", "决定这个槽在游戏里归到哪一类（装甲 / 武器 / 机动…）"),
    "Modifications.UIName": ("槽位显示名", "小写后能在本地化表里查到就是键，否则是字面文本"),
    "Modifications.Order": ("槽位顺序", "卡片上「自定义选项」那一排的先后"),
    "Modifications.ThumbnailFileName": ("槽位缩略图", "❗全表同值（占位）"),
    "Options.ModificationId": ("属于哪个槽", "→ Modifications"),
    "Options.Name": ("选项内部名", ""),
    "Options.UIName": ("选项显示名", "卡片上那一项的字面文本，或一个本地化键"),
    "Options.Order": ("选项顺序", "下拉里的先后"),
    "Options.IsDefault": (
        "默认选中",
        "缺省情况下选中的那一项。成本预览 = Cost + 所有默认项的 Cost",
    ),
    "Options.Cost": ("改装件费用", "卡片成本 = Units.Cost + 默认项费用之和"),
    "Options.ReplaceUnitName": ("换单位后的名字", "这一项把整个单位换成另一个时，卡片抬头写什么"),
    "Options.ConcatenateWithUnitName": ("名字追加段", "拼在单位名后面的那一段"),
    "Options.OptionPicture": ("选项图标", "→ catalog 地址键"),
    "Options.ThumbnailOverride": ("缩略图覆盖", "非空 = 卡墙那张缩略图换成它"),
    "Options.PortraitOverride": ("立绘覆盖", "非空 = 立绘换成它"),
    "Options.AudioPresetOverride": ("音效覆盖", "非空 = 音效换成它"),
    "Options.StealthOverride": ("隐蔽覆盖", "非 0 = 盖掉 Units.CurrentStealth"),
    "Options.TimeModifierRefund": ("撤回时间修正", "秒。加在撤回时间上"),
    "Options.TimeModifierResurrect": ("复活时间修正", "秒。加在阵亡整备时间上"),
    "Options.ArmorId": ("覆盖·装甲", "非 0 = 换成这套装甲（血量与六面全跟着变）"),
    "Options.MobilityId": ("覆盖·机动", "非 0 = 换成这套机动"),
    "Options.MainSensorId": (
        "覆盖·主传感器",
        "非 0 = 整套替换这个单位的传感器列表（是赋值不是追加）",
    ),
    "Options.ExtraSensorId": ("覆盖·附加传感器", "全库只有 1 条在用"),
    "Options.Ability1Id": ("覆盖·能力 1", ""),
    "Options.Ability2Id": ("覆盖·能力 2", ""),
    "Options.Ability3Id": ("覆盖·能力 3", ""),
    "Options.ReplaceUnitId": (
        "整单位替换",
        "非 0 = 这一项不是「装个东西」，而是换成另一个单位（变体）",
    ),
    "Options.IsDynamicTurretOption": ("动态炮塔选项", "全库 10 条"),
    "TransportAvailabilities.UnitId": ("被运输的单位", "→ Units"),
    "TransportAvailabilities.SpecializationAvailabilityId": (
        "哪条专精可用性",
        "→ SpecializationAvailabilities；表达「这个专精能运它」",
    ),
    "SpecializationAvailabilities.UnitId": ("单位", "→ Units"),
    "SpecializationAvailabilities.SpecializationId": ("专精", "→ Specializations"),
    "SpecializationAvailabilities.MaxAvailabilityXp0": ("XP0 可用数", "这一档能带几个"),
    "SquadMembers.UnitId": ("属于哪个班", "→ Units"),
    "SquadMembers.ModelFileName": (
        "士兵模型",
        "换它 = 换这个兵长什么样（SquadMembers 上改，不是 Units）",
    ),
    "SquadMembers.PrimaryWeaponId": ("主武器", "→ Weapons"),
    "SquadMembers.SpecialWeaponId": ("副武器", "→ Weapons。0 = 没有"),
    "SquadMembers.DeathPriority": ("阵亡优先级", "先死谁后死谁"),
    "Weapons.Name": ("内部名", ""),
    "Weapons.HUDName": ("显示名", "卡片武器行上写的那个"),
    "Weapons.Type": ("武器类型", "决定卡片上的中文类别（步枪/机枪/主炮/导弹…）"),
    "Weapons.HUDIcon": ("剪影图标", "→ catalog 地址键"),
    "Weapons.ModelFileName": ("模型键", ""),
    "Weapons.AudioPreset": ("音效预设", ""),
    "Weapons.HasPriorityOnChassis": ("车体优先", "炮塔与车体同时能打时优先用谁"),
    "Weapons.AimTimeMin": ("瞄准时间下限", "秒。与上限相同则卡片只打一个数"),
    "Weapons.AimTimeMax": ("瞄准时间上限", "秒"),
    "Weapons.MultiTargetTracking": ("多目标跟踪", "能同时锁定几个目标"),
    "Weapons.SimultaneousTracking": ("同时跟踪", ""),
    "Weapons.CanShootOnTheMove": ("行进间射击", "false = 必须停车打"),
    "Weapons.StabilizerQuality": ("稳定器质量", "行进间射击的精度"),
    "Weapons.FlashPerShot": ("每发火光", "视觉强度"),
    "Weapons.LowerVerticalAngle": ("俯角", "度。负数是能压多低"),
    "Weapons.UpperVerticalAngles": ("仰角", "度"),
    "Weapons.VerticalRotationSpeed": ("俯仰速度", "度/秒"),
    "Weapons.MagazineSize": ("弹匣容量", "卡片「弹匣容量」那一格"),
    "Weapons.MagazineReloadTimeMin": ("装填时间下限", "秒"),
    "Weapons.MagazineReloadTimeMax": ("装填时间上限", "秒"),
    "Weapons.ShotsPerBurstMin": ("点射发数下限", "喂给「射速」，不是 AI 的连发数"),
    "Weapons.ShotsPerBurstMax": ("点射发数上限", ""),
    "Weapons.TimeBetweenBurstsMin": ("点射间隔下限", "秒"),
    "Weapons.TimeBetweenBurstsMax": ("点射间隔上限", "秒"),
    "Weapons.TimeBetweenShotsInBurst": ("点射内间隔", "秒"),
    "Weapons.ShortGroundAttackBurst": ("短点射时长", "只对榴弹炮/迫击炮/多管火箭炮发射"),
    "Weapons.NormalGroundAttackBurst": ("标准点射时长", ""),
    "Weapons.LongGroundAttackBurst": ("长点射时长", ""),
    "Weapons.AutoLoaded": ("自动装填", ""),
    "Weapons.Silent": ("无声", ""),
    "Weapons.CQC": ("近战武器", ""),
    "Weapons.IsRadarDependent": ("依赖雷达", ""),
    "Weapons.IsVerticalLauncher": ("垂直发射", ""),
    "Weapons.IsLowAltDirectWeapon": ("低空直射", ""),
    "Weapons.IsHighAltDirectWeapon": ("高空直射", ""),
    "Weapons.IsUnderbarrel": ("下挂", "挂在别的枪下面"),
    "Weapons.CanBeMerged": ("可合并", "同型武器在卡片上并成一行"),
    "Weapons.WeaponChannel": ("武器通道", "按挂载点覆盖 —— 同一门枪挂在别处可以不同"),
    "Weapons.WeaponPriority": ("武器优先级", "同上"),
    "Ammunitions.Name": ("内部名", ""),
    "Ammunitions.HUDName": ("显示名", "卡片弹种行上写的那个"),
    "Ammunitions.HUDIcon": ("弹药图标", ""),
    "Ammunitions.HUDMultiplier": ("携弹量倍率", "❗弹量与射速都要乘它（AK = 3）"),
    "Ammunitions.UiOptions": ("UI 选项", "位掩码，控制卡片上画哪几行"),
    "Ammunitions.ModelFileName": ("弹体模型", ""),
    "Ammunitions.AudioPreset": ("音效预设", ""),
    "Ammunitions.FXPreset": ("特效预设", ""),
    "Ammunitions.VfxCountPerShot": ("每发特效数", ""),
    "Ammunitions.VfxCountPerShotMax": ("每发特效数上限", ""),
    "Ammunitions.Airburst": ("空爆", ""),
    "Ammunitions.SupplyCost": ("补给值", "kg。卡片「补给值」那一格"),
    "Ammunitions.ResupplyTime": ("补给时间", "秒。>1 才在卡片上发射"),
    "Ammunitions.Damage": ("伤害", "卡片「伤害」那一格（四舍五入到 0.05）"),
    "Ammunitions.StressDamage": ("压制伤害", "打士气，不是打血"),
    "Ammunitions.DamageOverTimeDuration": ("持续伤害时长", "秒"),
    "Ammunitions.IgnoreCover": (
        "无视掩体",
        "0~1。卡片上 ×100 加 %，>0 才发射（实测 0.12 / 0.2 / 1.0）",
    ),
    "Ammunitions.TargetType": ("可瞄准目标", "位掩码。❗1 是「地面」不是「步兵」，步兵是 2"),
    "Ammunitions.ArmorTargeted": ("打哪种装甲", "决定卡片「穿深」那一行写动能还是爆炸伤害"),
    "Ammunitions.PenetrationAtMinRange": (
        "最近距离穿深",
        "mm。与地面距离穿深一起拼成「50 - 100 mm」",
    ),
    "Ammunitions.PenetrationAtGroundRange": ("地面距离穿深", "mm"),
    "Ammunitions.NoDamageFalloff": ("无衰减", ""),
    "Ammunitions.TopArmorAttack": ("攻顶", "打顶部装甲"),
    "Ammunitions.IsTopArmorArmorAttack": ("攻顶（另一种）", ""),
    "Ammunitions.HealthAOERadius": ("爆炸半径", "m。卡片上 ×2"),
    "Ammunitions.StressAOERadius": ("压制半径", "m"),
    "Ammunitions.OverpressureRadius": ("超压半径", "m"),
    "Ammunitions.RadioFuseDistance": ("无线电引信距离", "m"),
    "Ammunitions.CriticMultiplier": ("暴击倍率", ""),
    "Ammunitions.CanBeIntercepted": ("可被 APS 拦截", "true 时卡片上发一行，值就是「+」"),
    "Ammunitions.CanBeTargeted": ("可被拦截", ""),
    "Ammunitions.TrajectoryType": ("弹道类型", "卡片「类型」那一行"),
    "Ammunitions.MinimalRange": ("最小射程", "m。卡片上 ×2"),
    "Ammunitions.GroundRange": ("地面射程", "m。卡片上 ×2"),
    "Ammunitions.LowAltRange": ("低空射程", "m。卡片上 ×2"),
    "Ammunitions.HighAltRange": ("高空射程", "m。卡片上 ×2"),
    "Ammunitions.OverflyHeight": ("越飞高度", "m"),
    "Ammunitions.SeadProjectileRangeOverride": ("反辐射射程覆盖", "全库 1 条在用"),
    "Ammunitions.NonSeadProjectileRangeOverride": ("非反辐射射程覆盖", "❗全表 0（占位）"),
    "Ammunitions.MuzzleVelocity": ("初速", "m/s"),
    "Ammunitions.MaxSpeed": ("最大速度", "m/s"),
    "Ammunitions.Acceleration": ("加速度", "m/s²"),
    "Ammunitions.BurnTime": ("发动机工作时间", "秒"),
    "Ammunitions.DispersionHorizontalRadius": ("水平散布", "m。卡片上 ×2，拼成 V/H"),
    "Ammunitions.DispersionVerticalRadius": ("垂直散布", "m"),
    "Ammunitions.DispersionMinimal": ("最小散布", "m"),
    "Ammunitions.LaunchMarginAngle": ("发射边角", "度"),
    "Ammunitions.LoftAngle": ("抛射角", "度"),
    "Ammunitions.LoftHeight": ("抛射高度", "m"),
    "Ammunitions.AimTimeMinOverride": ("瞄准时间下限覆盖", "非 0 = 盖掉武器那一列"),
    "Ammunitions.AimTimeMaxOverride": ("瞄准时间上限覆盖", ""),
    "Ammunitions.GenerateSmoke": ("生成烟幕", ""),
    "Ammunitions.SmokeRadius": ("烟幕半径", "m"),
    "Ammunitions.SmokeDuration": ("烟幕时长", "秒"),
    "Ammunitions.SmokeFadeDuration": ("烟幕消散时长", "秒"),
    "Ammunitions.Seeker": ("制导类型", "导弹的导引头种类"),
    "Ammunitions.LaserGuided": ("激光制导", ""),
    "Ammunitions.SeekerAngle": ("导引头视场", "度"),
    "Ammunitions.MaxSeekerDistance": ("导引头最大距离", "m"),
    "Ammunitions.CanReaquire": ("可重新捕获", "丢目标后能不能再锁"),
    "Ammunitions.AimStartDelay": ("开始瞄准延迟", "秒"),
    "Ammunitions.MainEngineIgnitionDelay": ("主发动机点火延迟", "秒"),
    "Ammunitions.RotationSpeed": ("旋转速度", "度/秒"),
    "Ammunitions.PreEgnitionRotationSpeed": ("点火前转速", ""),
    "Ammunitions.PreventReloadWhileTracked": ("被跟踪时不能装填", ""),
    "Ammunitions.StripingIgnore": ("忽略条纹", "全库 1 条"),
    "Armors.Id": ("装甲 Id", "→ 被 UnitArmors.ArmorId 引用"),
    "Armors.IsDefault": ("默认装甲", "每个单位至少要有一条 true"),
    "Armors.Name": ("套装名", "例：Armor BTR80 Base"),
    "Armors.ModelFileName": ("模型键", "预制体里的节点名"),
    "Armors.ArmorValue": ("装甲值", "66/319 行非 0"),
    "Armors.MaxHealthPoints": ("血量", "卡片「生命值」那一格的原料"),
    "Armors.HeatArmorFront": ("破甲·正面", "HEAT 装甲"),
    "Armors.HeatArmorRear": ("破甲·背面", ""),
    "Armors.HeatArmorSides": ("破甲·侧面", ""),
    "Armors.HeatArmorTop": ("破甲·顶部", ""),
    "Armors.KinArmorFront": ("动能·正面", ""),
    "Armors.KinArmorRear": ("动能·背面", ""),
    "Armors.KinArmorSides": ("动能·侧面", ""),
    "Armors.KinArmorTop": ("动能·顶部", ""),
    "Mobility.Name": ("机动名", ""),
    "Mobility.IsDefault": ("默认机动", ""),
    "Mobility.IsAmphibious": ("两栖", "卡片「能力」那一排的「两栖」"),
    "Mobility.IsAirDroppable": ("可空投", ""),
    "Mobility.Weight": ("机动自带重量", "只有 4 行非 0"),
    "Mobility.HeavyLiftWeight": ("重吊重量", ""),
    "Mobility.TurnRate": ("转向速率", ""),
    "Mobility.Acceleration": ("加速度", ""),
    "Mobility.MaxCrossCountrySpeed": ("越野速度", "km/h"),
    "Mobility.MaxSpeedRoad": ("公路速度", "km/h。卡片「前进速度」"),
    "Mobility.MaxSpeedReverse": ("倒车速度", "km/h。卡片「倒车速度」"),
    "Mobility.MaxSpeedWater": ("水上速度", "km/h。**强假设**只用于舰船"),
    "Mobility.Agility": ("敏捷", ""),
    "Mobility.ClimbRate": ("爬升率", ""),
    "Mobility.IsChangeAltitude": ("可改变高度", ""),
    "Mobility.LoiteringTime": ("滞空时间", "秒"),
    "Mobility.IsAfterburner": ("有加力", ""),
    "Mobility.AfterBurningLoiteringTime": ("加力滞空时间", ""),
    "Mobility.FlyPresetId": ("飞行包线", "→ PlaneFlyPresets"),
    "Sensors.Name": ("观瞄名", ""),
    "Sensors.IsDefault": ("默认观瞄", ""),
    "Sensors.OpticsGround": ("地面光学", "卡片「视野」= max(地面, 高空) × 2"),
    "Sensors.OpticsHighAltitude": ("高空光学", ""),
    "Sensors.OpticsLowAltitude": ("低空光学", ""),
    "Sensors.ModelFileName": ("模型键", "预制体里的节点名"),
    "Turrets.Name": ("炮塔名", ""),
    "Turrets.ModelFileName": ("模型键", "Turret Empty 是占位空炮塔"),
    "Turrets.IsDefault": ("默认炮塔", ""),
    "Turrets.IsDynamicTurret": ("动态炮塔", "判定是递归的"),
    "Turrets.ParentTurretId": ("父炮塔", "→ Turrets 自引用。表达「挂在哪」，不是「挂不挂」"),
    "Turrets.FullRotation": ("全向旋转", ""),
    "Turrets.LeftHorizontalAngle": ("左转界", "度"),
    "Turrets.RightHorizontalAngle": ("右转界", "度"),
    "Turrets.HorizontalRotationSpeed": ("水平转速", "度/秒"),
    "TurretWeapons.TurretId": ("炮塔", "→ Turrets"),
    "TurretWeapons.WeaponId": ("武器", "→ Weapons"),
    "TurretWeapons.Order": ("顺序", ""),
    "TurretWeapons.WeaponChannel": ("通道覆盖", "按挂载点覆盖武器行同名值（328/1279 不同）"),
    "TurretWeapons.WeaponPriority": ("优先级覆盖", ""),
    "Abilities.Name": ("能力名", ""),
    "Abilities.IsDefault": ("默认能力", ""),
    "Abilities.ECMAccuracyMultiplier": ("ECM 精度倍率", ""),
    "Abilities.IsRadar": ("是雷达", ""),
    "Abilities.IsLaserDesignator": ("激光指示器", "卡片「激光指示」那个 chip"),
    "Abilities.LaserMaxRange": ("激光最大距离", "m"),
    "Abilities.IsSmoke": ("是烟幕", "卡片「烟雾弹 ×N」"),
    "Abilities.SmokeAmmunitionId": ("烟幕弹种", "→ Ammunitions"),
    "Abilities.SmokeAmmunitionQuantity": ("烟幕弹数", ""),
    "Abilities.IsAPS": ("是主动防御", ""),
    "Abilities.APSQuantity": ("APS 拦截数", ""),
    "Abilities.IsDecoy": ("是诱饵", ""),
    "Abilities.IsResupplyAbility": ("是补给能力", "卡片「重新补给」"),
    "Abilities.ResupplyRadius": ("补给半径", "m"),
    "Abilities.SupplyPointCost": ("补给点消耗", ""),
    "Abilities.SupplyTargetsMask": ("补给目标掩码", "位掩码：能补给哪几类"),
    "Abilities.SneakCoverBonus": ("潜行掩体加成", ""),
    "Countries.Name": ("阵营键", "RUS / USA / Editor"),
    "Countries.UIName": ("阵营显示名", ""),
    "Countries.FlagFileName": ("国旗图", "→ catalog 地址键"),
    "Countries.MaxPoints": ("阵营点数上限", ""),
    "Countries.Hidden": ("隐藏", ""),
    "Specializations.Name": ("专精键", ""),
    "Specializations.UIName": ("专精显示名", ""),
    "Specializations.UIDescription": ("专精说明", ""),
    "Specializations.Icon": ("专精图标", ""),
    "Specializations.Illustration": ("专精插画", ""),
    "Specializations.ShowInHangar": ("在机库显示", ""),
    "Specializations.MaxSlots": ("总槽位", "卡组预算"),
    "Specializations.ReconPoints": ("侦察点数", "卡组预算"),
    "PlaneFlyPresets.Name": ("包线名", ""),
    "PlaneFlyPresets.MaxSpeed": ("最大速度", "卡片「前进速度」对固定翼读它"),
    "PlaneFlyPresets.MinSpeed": ("最小速度", ""),
    "PlaneFlyPresets.Acceleration": ("加速度", ""),
    "PlaneFlyPresets.ClimbRate": ("爬升率", ""),
}

#: 兜底的枚举/掩码（队友的全量表到了会自动盖过去）
FALLBACK_ENUM: dict[str, dict] = {
    "Ammunitions.TrajectoryType": {
        "cn": "弹道类型",
        "kind": "enum",
        "doc": "卡片「类型」那一行",
        "values": {
            "10": "直射弹药",
            "20": "火炮",
            "30": "迫击炮",
            "40": "多管火箭炮",
            "100": "导弹",
            "110": "导弹",
            "120": "导弹",
            "200": "巡航导弹",
            "300": "弹道导弹",
            "400": "炸弹",
            "410": "减速炸弹",
        },
    },
    "Ammunitions.TargetType": {
        "cn": "可瞄准目标",
        "kind": "mask",
        "doc": "位掩码；卡片弹种行那排图标。❗1 是「地面」不是步兵",
        "values": {
            "1": "地面",
            "2": "步兵",
            "4": "载具",
            "8": "直升机",
            "16": "飞机",
            "32": "舰船",
            "128": "投射物",
            "256": "防空压制",
            "512": "巡航导弹",
            "1024": "弹道导弹",
        },
    },
    "Weapons.Type": {
        "cn": "武器类型",
        "kind": "enum",
        "doc": "卡片武器行上的中文类别",
        "values": {
            "1": "步枪",
            "2": "精确射手步枪",
            "3": "狙击步枪",
            "4": "反器材步枪",
            "5": "突击步枪",
            "6": "战斗步枪",
            "25": "冲锋枪",
            "26": "轻机枪",
            "27": "通用机枪",
            "28": "重机枪",
            "29": "加特林",
            "30": "自动步枪",
            "40": "霰弹枪",
            "60": "榴弹发射器",
            "61": "榴弹发射器",
            "62": "火箭筒",
            "63": "火箭筒",
            "64": "手榴弹",
            "80": "反坦克导弹",
            "81": "反坦克导弹",
            "82": "巡航导弹",
            "83": "弹道导弹",
            "84": "反辐射导弹",
            "85": "便携防空",
            "86": "防空导弹",
            "87": "空空雷达弹",
            "88": "空空红外弹",
            "89": "空地导弹",
            "110": "主炮",
            "111": "榴弹炮",
            "112": "迫击炮",
            "113": "多管火箭炮",
            "114": "机关炮",
            "115": "高射炮",
            "116": "机炮",
            "140": "火箭巢",
            "141": "机炮吊舱",
            "160": "炸弹",
            "161": "减速炸弹",
            "162": "制导炸弹",
        },
    },
    "Units.Type": {
        "cn": "单位类型",
        "kind": "enum",
        "doc": "决定需要哪些子树",
        "values": {"2": "步兵", "4": "载具", "8": "直升机", "16": "飞机", "32": "舰船"},
    },
    "Units.CategoryType": {
        "cn": "军械库分类",
        "kind": "enum",
        "doc": "卡墙左边那一列",
        "values": {
            "0": "侦察",
            "1": "步兵",
            "2": "载具",
            "3": "支援",
            "4": "未分类",
            "5": "直升机",
            "6": "战机",
        },
    },
    "Units.Role": {"cn": "角色", "kind": "other", "doc": "卡片副标题里那个小标签", "values": {}},
    "Modifications.Type": {
        "cn": "槽位类型",
        "kind": "enum",
        "doc": "这个改装槽归哪一类",
        "values": {"1": "武器", "2": "装甲", "3": "机动", "4": "传感器", "5": "装甲组件"},
    },
}


def cn(table: str, column: str) -> str:
    node = LABELS.get(table, {}).get(column)
    if isinstance(node, dict) and node.get("cn"):
        return node["cn"]
    hit = FALLBACK.get("%s.%s" % (table, column))
    return hit[0] if hit else column


def doc_of(table: str, column: str) -> str:
    node = LABELS.get(table, {}).get(column)
    if isinstance(node, dict) and node.get("doc"):
        return node["doc"]
    hit = FALLBACK.get("%s.%s" % (table, column))
    return hit[1] if hit else ""


def enum_node(table: str, column: str) -> dict:
    node = ENUMS.get("%s.%s" % (table, column))
    if isinstance(node, dict) and node.get("values"):
        return node
    return FALLBACK_ENUM.get("%s.%s" % (table, column), {})


def decode(table: str, column: str, value) -> tuple[str, str]:
    """把一个值翻译成给人看的样子。返回 (文本, 种类)。

    种类 ∈ text / enum / mask / bool / ref / num
    """
    node = enum_node(table, column)
    if node:
        kind = node.get("kind", "enum")
        vals = node.get("values", {})
        if kind == "bool":
            return ("是" if value else "否"), "text"
        if kind == "mask":
            try:
                n = int(value)
            except (TypeError, ValueError):
                n = 0
            if n == 0:
                return "（不瞄准任何目标）", "mask"
            parts = [vals[str(1 << i)] for i in range(16) if n & (1 << i) and vals.get(str(1 << i))]
            return " + ".join(parts) if parts else str(n), "mask"
        text = vals.get(str(value))
        if text:
            return text, "enum"
    return str(value), "text"


#: 表的中文名（GUI 上显示这些，不显示表名）
TABLE_CN = {
    "Units": "单位本体",
    "UnitPropulsions": "机动归属",
    "UnitArmors": "装甲归属",
    "SensorUnits": "传感器归属",
    "UnitAbilities": "能力归属",
    "TurretUnits": "炮塔归属",
    "WeaponAmmunitions": "武器携弹",
    "SquadMembers": "班组成员",
    "SquadWeapons": "班用武器",
    "Modifications": "改装槽",
    "Options": "改装选项",
    "TransportAvailabilities": "运输配对",
    "SpecializationAvailabilities": "专精可用性",
    "Countries": "阵营",
    "Specializations": "师级专精",
    "Armors": "装甲模板",
    "Mobility": "机动模板",
    "Sensors": "观瞄模板",
    "Abilities": "能力模板",
    "Turrets": "炮塔模板",
    "Weapons": "武器模板",
    "Ammunitions": "弹药模板",
    "TurretWeapons": "炮塔挂载",
    "PlaneFlyPresets": "飞行包线",
}

#: 外键列 -> 它指向哪张表（界面上一律显示那边的名字，不显示 Id）
REF_TABLE = {
    "UnitId": "Units",
    "CountryId": "Countries",
    "ReplaceUnitId": "Units",
    "ArmorId": "Armors",
    "MobilityId": "Mobility",
    "MainSensorId": "Sensors",
    "ExtraSensorId": "Sensors",
    "SensorId": "Sensors",
    "FlyPresetId": "PlaneFlyPresets",
    "AbilityId": "Abilities",
    "Ability1Id": "Abilities",
    "Ability2Id": "Abilities",
    "Ability3Id": "Abilities",
    "SmokeAmmunitionId": "Ammunitions",
    "TurretId": "Turrets",
    "ParentTurretId": "Turrets",
    "WeaponId": "Weapons",
    "PrimaryWeaponId": "Weapons",
    "SpecialWeaponId": "Weapons",
    "AmmunitionId": "Ammunitions",
    "ModificationId": "Modifications",
    "SpecializationId": "Specializations",
    "SpecializationAvailabilityId": "SpecializationAvailabilities",
    "ThumbnailFileName": "—",
    "PortraitFileName": "—",
}
REF_TABLE.update({"Turret%dId" % i: "Turrets" for i in range(21)})

#: 各表「显示名」用哪一列（外键一律显示名字，不显示 Id）
NAME_COL = {
    "Units": "HUDName",
    "Weapons": "HUDName",
    "Ammunitions": "HUDName",
    "Countries": "UIName",
    "Specializations": "UIName",
    "Modifications": "UIName",
    "Options": "UIName",
    "Armors": "Name",
    "Mobility": "Name",
    "Sensors": "Name",
    "Abilities": "Name",
    "Turrets": "Name",
    "PlaneFlyPresets": "Name",
    "TurretWeapons": "Id",
    "WeaponAmmunitions": "Id",
    "UnitPropulsions": "Id",
    "UnitArmors": "Id",
    "SensorUnits": "Id",
    "UnitAbilities": "Id",
    "TurretUnits": "Id",
    "SquadMembers": "Id",
    "SquadWeapons": "Id",
    "Modifications2": "Id",
    "TransportAvailabilities": "Id",
    "SpecializationAvailabilities": "Id",
}


# ---------------------------------------------------------------- 数据
def load_all():
    schema = load_schema(DB)
    inst = Instance(
        path_id="52085", directory=DB / "52085", schema=schema, manifest=load_manifest(DB / "52085")
    )
    data = CardData(inst)
    return inst, data, build_unit_card(data, 1)


def row_of(inst, table: str, row_id: int) -> dict:
    tb = inst.table(table)
    i = tb.columns.index("Id")
    for r in tb.rows:
        if r[i] == row_id:
            return dict(zip(tb.columns, r, strict=False))
    return {}


def rows_where(inst, table: str, column: str, value) -> list:
    tb = inst.table(table)
    ci = tb.columns.index(column)
    return [dict(zip(tb.columns, r, strict=False)) for r in tb.rows if r[ci] == value]


def ref_name(inst, table: str, row_id: int) -> str:
    """外键指向的那一行叫什么（按名字，不按 Id）。

    ⚠️ 同样**只有一份实现**：core/edit/foreign.py::display_name。
    界面上的外键、图上的外键、搜索框里的候选，三处必须说同一句话。
    """
    return core_display_name(inst, table, row_id)


def wall_units(data, limit=42):
    from ba_mod_maker.app.viewmodel.unit_card import card_cost

    units = data.t("Units")
    out = []
    for row in units.all():
        if not units.b(row, "DisplayInArmory"):
            continue
        uid = units.i(row, "Id")
        key = units.s(row, "ThumbnailFileName")
        if not key or icon("unit_labels", key) is None:
            continue
        out.append(
            (uid, units.s(row, "HUDName") or units.s(row, "Name"), key, int(card_cost(data, uid)))
        )
        if len(out) >= limit:
            break
    return out


def fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        return "%g" % v
    return str(v)


# ---------------------------------------------------------------- UI 零件
def window(img, d, x, y, w, h, title):
    rr(d, (x, y, x + w, y + h), 10, fill=BG, outline=BORDER2, width=2)
    d.rectangle((x + 2, y + 2, x + w - 2, y + 32), fill=ELEV)
    d.line((x, y + 34, x + w, y + 34), fill=BORDER, width=1)
    t(d, (x + 16, y + 7), title, F(18, latin=True, bold=True), TX2)
    for i, col in enumerate((RED, WARN, GREEN)):
        d.ellipse((x + w - 74 + i * 22, y + 11, x + w - 62 + i * 22, y + 23), fill=col)
    return (x + 2, y + 36, x + w - 2, y + h - 2)


def caption(d, x, y, head, body, color=TX2):
    t(d, (x, y), head, F(22, bold=True), color)
    t(d, (x + d.textlength(head, font=F(22, bold=True)) + 14, y + 3), body, F(18), TX3)


def crumb(d, x, y, parts, color=TX3):
    cx = x
    for i, (text, col, bold) in enumerate(parts):
        if i:
            t(d, (cx, y), "›", F(17), TX3)
            cx += 20
        t(d, (cx, y), text, F(17, bold=bold), col)
        cx += d.textlength(text, font=F(17, bold=bold)) + 14
    return cx


def bang(d, cx, cy, kind="error", r=9):
    fill = RED if kind == "error" else WARN
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)
    t(
        d,
        (cx, cy - r + 1),
        "!",
        F(int(r * 1.6), latin=True, bold=True),
        (255, 255, 255),
        anchor="ma",
    )


def tooltip(img, d, box, lines, accent=ACCENT, title=None):
    x0, y0, x1, y1 = box
    rr(d, (x0 + 4, y0 + 5, x1 + 4, y1 + 5), 8, fill=(0, 0, 0, 110))
    rr(d, box, 8, fill=ELEV, outline=accent, width=2)
    y = y0 + 10
    if title:
        t(d, (x0 + 14, y), title, F(18, bold=True), accent)
        y += 26
    for ln in lines:
        text, color = ln if isinstance(ln, tuple) else (ln, TX)
        for piece in wrap(d, text, F(16), x1 - x0 - 28):
            t(d, (x0 + 14, y), piece, F(16), color)
            y += 23
    return y


def field_cells(d, box, fields, *, per_row=2, cell_h=50, limit=None):
    """一组词条。每条 = 中文名 + 值（外键显示名字）+ 小字「表#Id · 原列名」。

    fields: [(cn, value, kind, sub)]
    """
    x0, y0, x1, y1 = box
    col_w = (x1 - x0) // per_row
    shown = fields if limit is None else fields[:limit]
    for i, (name, value, kind, sub) in enumerate(shown):
        c, r = i % per_row, i // per_row
        bx = x0 + c * col_w
        by = y0 + r * cell_h
        if by + cell_h > y1:
            break
        rr(d, (bx + 3, by + 2, bx + col_w - 6, by + cell_h - 4), 4, fill=INPUT)
        t(d, (bx + 12, by + 6), name, F(15), TX3)
        color = {"ref": TXS, "mask": PURPLE, "enum": BLUE, "shared": ORANGE, "text": TX}[kind]
        t(
            d,
            (bx + 12 + 150, by + 3),
            cut(d, value, F(18, bold=kind in ("ref", "mask", "enum")), col_w - 190),
            F(18, bold=kind in ("ref", "mask", "enum")),
            color,
        )
        if kind == "ref":
            ax = bx + col_w - 26
            d.polygon([(ax, by + 10), (ax + 10, by + 10), (ax + 5, by + 17)], fill=ACCENT)
        t(d, (bx + 12, by + 28), cut(d, sub, F(14), col_w - 40), F(14), TX3)
    rows = (len(shown) + per_row - 1) // per_row
    return y0 + rows * cell_h


def group_header(d, box, title, note, right="", color=ACCENT):
    x0, y0, x1, _ = box
    d.rectangle((x0, y0, x1, y0 + 30), fill=SELECTED)
    d.rectangle((x0, y0, x0 + 4, y0 + 30), fill=color)
    t(d, (x0 + 12, y0 + 4), title, F(18, bold=True), TXS)
    if note:
        t(d, (x0 + 12 + d.textlength(title, font=F(18, bold=True)) + 12, y0 + 6), note, F(15), TX3)
    if right:
        t(d, (x1 - 12, y0 + 6), right, F(15), TX3, anchor="ra")
    return y0 + 30


# ---------------------------------------------------------------- 立绘上的提示层
def hint_chips(d, box, items, *, compact=False):
    """贴在立绘上的提示条。items: [(kind, head, body)]，kind ∈ bad/share/note。"""
    x0, y0, x1, _ = box
    colors = {"bad": RED, "share": ORANGE, "note": BLUE}
    y = y0
    for kind, head, body in items:
        col = colors[kind]
        h = 30 if compact else 34
        rr(d, (x0, y, x1, y + h), 6, fill=(11, 14, 18, 235), outline=col, width=2)
        if kind == "bad":
            bang(d, x0 + 18, y + h // 2, "error", 9)
        else:
            d.ellipse(
                (x0 + 9, y + h // 2 - 9, x0 + 27, y + h // 2 + 9),
                fill=hexc(C.bg_elevated),
                outline=col,
                width=2,
            )
            t(
                d,
                (x0 + 18, y + h // 2 - 9),
                "i" if kind == "note" else "◉",
                F(15, latin=True, bold=True),
                col,
                anchor="ma",
            )
        t(d, (x0 + 36, y + (5 if compact else 6)), head, F(17, bold=True), col)
        t(
            d,
            (x0 + 36 + d.textlength(head, font=F(17, bold=True)) + 12, y + (7 if compact else 8)),
            cut(d, body, F(15), x1 - x0 - 70 - d.textlength(head, font=F(17, bold=True))),
            F(15),
            TX,
        )
        y += h + 6
    return y


def ref_picker(d, box, label, query, rows, note, *, sel=0, accent=ACCENT):
    """外键的控件：**输入名字筛**，不让人查 Id。"""
    x0, y0, x1, y1 = box
    rr(d, box, 8, fill=ELEV, outline=accent, width=2)
    t(d, (x0 + 14, y0 + 10), label, F(17, bold=True), TX2)
    rr(d, (x0 + 14, y0 + 36, x1 - 14, y0 + 68), 5, fill=INPUT, outline=accent, width=2)
    t(d, (x0 + 26, y0 + 42), query, F(19), TXS)
    cx = x0 + 26 + d.textlength(query, font=F(19)) + 2
    d.line((cx, y0 + 42, cx, y0 + 62), fill=accent, width=2)
    t(d, (x1 - 30, y0 + 43), "✕", F(17), TX3)
    ry = y0 + 76
    for i, (name, sub, hit) in enumerate(rows):
        rr(
            d,
            (x0 + 14, ry, x1 - 14, ry + 34),
            5,
            fill=SELECTED if i == sel else INPUT,
            outline=accent if i == sel else None,
            width=2 if i == sel else 1,
        )
        t(d, (x0 + 26, ry + 6), name, F(18, bold=(i == sel)), TXS if i == sel else TX)
        t(d, (x1 - 26, ry + 8), sub, F(14, latin=True), TX3, anchor="ra")
        if hit:
            t(d, (x1 - 190, ry + 9), hit, F(13), TX3, anchor="ra")
        ry += 38
    t(d, (x0 + 14, ry + 2), note, F(14), TX3)
    return ry + 24


def count_cells(inst, unit_id: int) -> tuple[int, int]:
    """这个单位牵扯到多少张表、多少个可改的格子。

    ⚠️ **边界只有一份实现**：core/edit/scope.py::unit_scope（零 Qt，带用例）。
    本函数原来自己算过一版，与产品口径对不上（少算了 TurretWeapons，
    也没有"随行行不再往外扩"那条限制）—— 现在只转发，绝不再写第二份。
    """
    scope = unit_scope(inst, unit_id)
    return scope.table_count, scope.cell_count


def _count_cells_legacy(inst, unit_id: int) -> tuple[int, int]:
    """（旧实现，保留在文件里只为让 git 的 diff 说清楚改了什么，不再被调用）

    三步走，**只走属于这个单位的东西**：
      ① 本单位那一行；
      ② 11 张带 UnitId 的关联表里属于它的行；
      ③ 这些行上的 *Id 再往外走两跳（Modifications → Options；
         UnitPropulsions → Mobility → PlaneFlyPresets；TurretUnits → Turrets → TurretWeapons → Weapons；
         WeaponAmmunitions → Weapons/Ammunitions …）。
    ReplaceUnitId 指向的是**别的单位**，不算进来。
    """
    seen: set[tuple[str, int]] = {("Units", unit_id)}
    front: list[tuple[str, int]] = [("Units", unit_id)]
    for table in inst.table_names:
        tb = inst.table(table)
        if "UnitId" not in tb.columns:
            continue
        ui, ii = tb.columns.index("UnitId"), tb.columns.index("Id")
        for row in tb.rows:
            if row[ui] == unit_id:
                seen.add((table, row[ii]))
                front.append((table, row[ii]))
    for _ in range(2):
        nxt: list[tuple[str, int]] = []
        for table, rid in front:
            for col, value in row_of(inst, table, rid).items():
                if not col.endswith("Id") or col == "Id" or col == "ReplaceUnitId":
                    continue
                if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                    continue
                target = REF_TABLE.get(col)
                if not target or target == "Units" or target == "—":
                    continue
                if target not in inst.table_names or (target, value) in seen:
                    continue
                seen.add((target, value))
                nxt.append((target, value))
        if not nxt:
            break
        front = nxt
    cells = 0
    for table, rid in sorted(seen):
        tb = inst.table(table)
        idx = tb.columns.index("Id")
        if any(r[idx] == rid for r in tb.rows):
            cells += len(tb.columns)
    return len({t for t, _ in seen}), cells


# ---------------------------------------------------------------- 单位卡片（可写）
def draw_card(img, d, box, card, mode="edit", *, hints=(), hint_open=False):
    x0, y0, x1, y1 = box
    rr(d, box, 8, fill=PANEL, outline=BORDER, width=1)
    pad = 16
    ix0, ix1 = x0 + pad, x1 - pad
    y = y0 + pad
    t(d, (ix0, y), card.title, F(30, bold=True), TXS)
    cw = d.textlength(str(card.cost), font=F(28, latin=True, bold=True))
    rr(d, (ix1 - cw - 28, y - 2, ix1, y + 34), 6, fill=ORANGE_BG)
    t(d, (ix1 - 14, y + 2), str(card.cost), F(28, latin=True, bold=True), ORANGE, anchor="ra")
    y += 40
    t(d, (ix0, y), card.subtitle, F(18), TX2)
    y += 28
    ph = int((ix1 - ix0) * (0.36 if hints else 0.30))
    rr(d, (ix0, y, ix1, y + ph), 6, fill=INPUT, outline=BORDER)
    im = icon("unit_portraits", card.portrait_key)
    if im is not None:
        paste_fit(img, im, (ix0 + 2, y + 2, ix1 - 2, y + ph - 2))
    if hints:
        # ★ 立绘上只挂校验角标；没有校验问题就是一个字都不画（hints 为空）
        hint_chips(d, (ix0 + 14, y + ph - len(hints) * 40 - 16, ix1 - 14, y + ph - 12), hints)
    y += ph + 18
    t(d, (ix0, y), "属性（%d）" % len(card.stats), F(20, bold=True), TX2)
    y += 30
    per, gap, chei = 4, 8, 68
    cwid = (ix1 - ix0 - gap * (per - 1)) // per
    for i, st in enumerate(card.stats):
        c, r = i % per, i // per
        bx = ix0 + c * (cwid + gap)
        by = y + r * (chei + gap)
        shared = bool(st.shared_with and st.shared_with > 1)
        color = ORANGE if shared else BLUE
        bg = ORANGE_BG if shared else INFO_BG
        rr(d, (bx, by, bx + cwid, by + chei), 6, fill=bg, outline=color, width=2)
        ims = icon("infocard_stats", st.sprite)
        if ims is not None:
            paste_fit(img, ims, (bx + 6, by + 5, bx + cwid - 6, by + 27), tint=TXS)
        t(
            d,
            ((bx + bx + cwid) // 2, by + 28),
            st.value,
            F(23, latin=True, bold=True),
            TXS,
            anchor="ma",
        )
        label = {"距离撤回至战斗群": "撤回时间"}.get(st.label, st.label)
        t(d, ((bx + bx + cwid) // 2, by + 50), label, F(14), TX2, anchor="ma")
        if st.key == "refund":
            t(d, (bx + 8, by + 6), "ƒ", F(17, latin=True, bold=True), ACCENT)
    y += ((len(card.stats) + per - 1) // per) * (chei + gap) + 6
    if card.abilities:
        t(d, (ix0, y), "能力", F(20, bold=True), TX2)
        y += 28
        cx = ix0
        for a in card.abilities:
            w = d.textlength(a.label, font=F(17)) + 24
            rr(d, (cx, y, cx + w, y + 30), 5, fill=ELEV, outline=BLUE, width=1)
            t(d, (cx + 12, y + 5), a.label, F(17), TX)
            cx += w + 8
        y += 42
    t(d, (ix0, y), "武器（%d）" % len(card.weapons), F(20, bold=True), TX2)
    if mode == "edit":
        t(d, (ix1 - 12, y + 4), "点一行 → 武器编辑器", F(15), ACCENT, anchor="ra")
    y += 30
    for w in card.weapons[:2]:
        rr(d, (ix0, y, ix1, y + 34), 5, fill=ELEV, outline=BORDER)
        imw = icon("weapon_icons", w.icon)
        if imw is not None:
            paste_fit(img, imw, (ix0 + 8, y + 7, ix0 + 82, y + 29), tint=TXS)
        t(d, (ix0 + 92, y + 7), w.label, F(19, bold=True), TXS)
        t(d, (ix1 - 12, y + 8), "×%d" % w.count, F(17, latin=True), TX2, anchor="ra")
        y += 40
        for a in w.ammo[:2]:
            ima = icon("ammo_icons", a.icon)
            if ima is not None:
                paste_fit(img, ima, (ix0 + 20, y + 2, ix0 + 66, y + 18), tint=TX2)
            t(d, (ix0 + 76, y), a.label, F(17), TX)
            if mode == "edit":
                t(d, (ix0 + 300, y), "→ 弹药编辑器", F(14), ACCENT)
            t(d, (ix1 - 12, y), "×%s" % a.count, F(17, latin=True), TX2, anchor="ra")
            y += 26
    if card.options:
        y += 8
        t(d, (ix0, y), "自定义选项（%d）" % len(card.options), F(20, bold=True), TX2)
        y += 28
        for g in card.options[:2]:
            t(d, (ix0, y + 5), g.label, F(17), TX2)
            bw2 = ix1 - ix0 - 200
            rr(d, (ix0 + 150, y, ix0 + 150 + bw2, y + 32), 5, fill=INPUT, outline=BORDER)
            t(d, (ix0 + 160, y + 6), cut(d, g.current_label, F(17), bw2 - 40), F(17), TX)
            ax, ay = ix0 + 150 + bw2 - 18, y + 12
            d.polygon([(ax, ay), (ax + 10, ay), (ax + 5, ay + 7)], fill=TX2)
            y += 40
    return y


# ---------------------------------------------------------------- 图 A · ① 军械库
def panel_armory(img, d, box, units, card):
    x0, y0, x1, y1 = box
    inner = window(img, d, x0, y0, x1 - x0, y1 - y0, "BA Mod Maker")
    ix0, iy0, ix1, iy1 = inner
    y = iy0 + 8
    t(d, (ix0 + 16, y), "文件", F(17), TX2)
    t(d, (ix0 + 70, y), "编辑", F(17), TX2)
    t(d, (ix0 + 124, y), "视图", F(17), TX2)
    t(d, (ix1 - 14, y), "实例 52085（默认·非战役）", F(16), TX2, anchor="ra")
    y += 30
    t(d, (ix0 + 16, y), "军械库", F(19, bold=True), TXS)
    d.rectangle((ix0 + 14, y + 26, ix0 + 96, y + 29), fill=ACCENT)
    t(d, (ix0 + 130, y), "数据表", F(19), TX2)
    t(d, (ix0 + 226, y), "检索", F(19), TX2)
    y += 40
    d.line((ix0, y, ix1, y), fill=BORDER)
    y += 10
    wall = (ix0 + 12, y, ix0 + 640, iy1 - 36)
    cbox = (ix0 + 652, y, ix1 - 12, iy1 - 36)
    t(d, (wall[0], wall[1]), "全部单位  395 · 未编入专精 57", F(18), TX2)
    tw, th, gp = 150, 100, 8
    cols = (wall[2] - wall[0] + gp) // (tw + gp)
    for i, u in enumerate(units[: cols * 7]):
        c, r = i % cols, i // cols
        bx = wall[0] + c * (tw + gp)
        by = wall[1] + 28 + r * (th + gp)
        selected = u[0] == 1
        rr(
            d,
            (bx, by, bx + tw, by + th),
            6,
            fill=CARD_SEL if selected else CARD_BG,
            outline=CARD_SEL_BORDER if selected else BORDER,
            width=2 if selected else 1,
        )
        cwid = d.textlength(str(u[3]), font=F(18, latin=True, bold=True))
        rr(d, (bx + tw - cwid - 20, by + 5, bx + tw - 6, by + 26), 4, fill=ORANGE_BG)
        t(d, (bx + tw - 12, by + 6), str(u[3]), F(18, latin=True, bold=True), ORANGE, anchor="ra")
        im = icon("unit_labels", u[2])
        if im is not None:
            paste_fit(
                img,
                im,
                (bx + 10, by + 28, bx + tw - 10, by + th - 24),
                tint=SIL_SEL if selected else SIL,
            )
        t(
            d,
            (bx + tw // 2, by + th - 22),
            cut(d, u[1], F(15), tw - 16),
            F(15),
            TXS if selected else TX,
            anchor="ma",
        )
    sb = (wall[0], wall[1] + 28, wall[0] + tw, wall[1] + 28 + th)
    rr(d, (sb[0] - 5, sb[1] - 5, sb[2] + 5, sb[3] + 5), 8, outline=CARD_SEL_BORDER, width=4)
    cy = wall[3] - 66
    rr(d, (sb[0] - 5, cy, sb[0] + 430, cy + 58), 8, fill=ORANGE_BG, outline=ORANGE, width=2)
    t(d, (sb[0] + 12, cy + 7), "在这里双击", F(23, bold=True), ORANGE)
    t(d, (sb[0] + 12, cy + 35), "单击 = 选中看卡；双击才进编辑器", F(15), TX2)
    d.line((sb[0] + 40, cy, sb[0] + 40, sb[3] + 8), fill=ORANGE, width=2)
    draw_card(img, d, cbox, card, "read")
    d.line((ix0, iy1 - 28, ix1, iy1 - 28), fill=BORDER, width=1)
    t(d, (ix0 + 12, iy1 - 22), "已加载 2 个实例 · 当前 52085", F(16), TX2)
    t(d, (ix1 - 12, iy1 - 22), "图标 游戏 bundle · 主题 dark", F(16), TX3, anchor="ra")
    return inner


# ---------------------------------------------------------------- 图 A · ② 单位编辑器
def panel_unit_editor(img, d, box, inst, card, hints):
    x0, y0, x1, y1 = box
    inner = window(img, d, x0, y0, x1 - x0, y1 - y0, "BA Mod Maker")
    ix0, iy0, ix1, iy1 = inner
    y = iy0 + 8
    t(d, (ix0 + 16, y + 1), "← 返回军械库", F(18, bold=True), ACCENT)
    crumb(
        d,
        ix0 + 186,
        y + 2,
        [("军械库", TX3, False), (card.title, TXS, True), ("单位编辑器", TX3, False)],
    )
    rr(d, (ix1 - 118, y - 2, ix1 - 14, y + 28), 5, fill=ACCENT)
    t(d, (ix1 - 66, y + 4), "保存", F(18, bold=True), (255, 255, 255), anchor="ma")
    rr(d, (ix1 - 300, y - 2, ix1 - 130, y + 28), 5, fill=ELEV, outline=BORDER)
    t(d, (ix1 - 215, y + 4), "实例 52085 + 52082", F(16), TX2, anchor="ma")
    y += 38
    d.line((ix0, y, ix1, y), fill=BORDER)
    y += 12
    cw = 940
    cbox = (ix0 + 12, y, ix0 + 12 + cw, iy1 - 12)
    draw_card(img, d, cbox, card, "edit", hints=hints)
    t(d, (cbox[0], cbox[3] + 2), "", F(14), TX3)
    rx0 = cbox[2] + 18
    t(d, (rx0, y - 2), "这个单位的全部词条", F(21, bold=True), TXS)
    t(
        d,
        (rx0 + d.textlength("这个单位的全部词条", font=F(21, bold=True)) + 14, y + 2),
        "中文名 + 悬浮注释；外键写名字不写 Id",
        F(16),
        TX3,
    )
    n_tab, n_cell = count_cells(inst, 1)
    t(
        d,
        (ix1 - 14, y + 2),
        "%d 张表 · %d 个格子 · 一个都不少" % (n_tab, n_cell),
        F(16),
        TX2,
        anchor="ra",
    )
    yy = y + 32
    rb = (rx0, yy, ix1 - 34, iy1 - 12)

    urow = row_of(inst, "Units", 1)

    units_cols = [
        "Id",
        "HUDName",
        "Name",
        "CountryId",
        "Type",
        "CategoryType",
        "Role",
        "DisplayInArmory",
        "Cost",
        "UpkeepMultiplier",
        "TimeModifierRefund",
        "TimeModifierResurrect",
        "MaxStress",
        "CurrentStealth",
        "Stealth",
        "InfantrySlots",
        "Weight",
        "Length",
        "Width",
        "Height",
        "ModelFileName",
        "PortraitFileName",
        "ThumbnailFileName",
        "AudioPreset",
        "IsUnitModification",
        "ContentMembership",
        "WaterDiveOffset",
        "OriginalName",
        "OriginalCost",
        "Description",
        "OwnerInfantryID",
    ]
    fields = []
    ref_tables = {"CountryId": "Countries"}
    for c in units_cols:
        sub = "单位本体#1 · %s" % c
        if c in ref_tables and urow.get(c):
            kind = "ref"
            value = ref_name(inst, ref_tables[c], urow[c])
            sub = "%s#%s · %s" % (TABLE_CN[ref_tables[c]], urow[c], c)
        elif enum_node("Units", c):
            value, kind = decode("Units", c, urow.get(c))
            sub = "单位本体#1 · %s = %s" % (c, fmt(urow.get(c)))
        else:
            value, kind = fmt(urow.get(c)), "text"
        fields.append((cn("Units", c), value, kind, sub))
    yy = group_header(
        d,
        (rb[0], yy, rb[2], rb[3]),
        "单位本体（Units#1）",
        "31 列 · 独占 · 悬浮任意一条看注释",
        right="这是卡片墙上的那个单位",
    )
    yy = field_cells(d, (rb[0], yy, rb[2], rb[3]), fields, per_row=3, cell_h=46)
    yy += 10

    def link_block(top, title, note, rows, right="", y_lim=None):
        y_lim = y_lim or rb[3]
        x0b, x1b = rb[0], rb[2]
        d.rectangle((x0b, top, x1b, top + 28), fill=SELECTED)
        d.rectangle((x0b, top, x0b + 4, top + 28), fill=BLUE)
        t(d, (x0b + 12, top + 3), title, F(17, bold=True), TXS)
        if note:
            t(
                d,
                (x0b + 12 + d.textlength(title, font=F(17, bold=True)) + 12, top + 5),
                note,
                F(14),
                TX3,
            )
        rr(d, (x1b - 112, top + 2, x1b - 46, top + 25), 4, fill=ELEV, outline=BLUE, width=1)
        t(d, (x1b - 79, top + 4), "+ 加一行", F(14), BLUE, anchor="ma")
        if right:
            t(d, (x1b - 122, top + 5), right, F(14), TX3, anchor="ra")
        top += 28
        for cells, _kind in rows:
            if top + 26 > y_lim:
                t(d, (x0b + 14, top + 2), "…（滚动继续）", F(14), TX3)
                return top + 24
            rr(d, (x0b + 4, top + 1, x1b - 6, top + 25), 4, fill=INPUT)
            cx = x0b + 14
            for text, cwid_, color in cells:
                t(d, (cx, top + 4), text, F(15), color)
                cx += cwid_
            t(d, (x1b - 22, top + 4), "×", F(15), TX3)
            top += 28
        return top + 4

    props = rows_where(inst, "UnitPropulsions", "UnitId", 1)
    armors = rows_where(inst, "UnitArmors", "UnitId", 1)
    sensors = rows_where(inst, "SensorUnits", "UnitId", 1)
    abils = rows_where(inst, "UnitAbilities", "UnitId", 1)
    turrets = rows_where(inst, "TurretUnits", "UnitId", 1)
    ammos = rows_where(inst, "WeaponAmmunitions", "UnitId", 1)
    mods = rows_where(inst, "Modifications", "UnitId", 1)
    yy = link_block(
        yy,
        "机动归属（UnitPropulsions）",
        "2 行 · 必需 · 独占",
        [
            (
                [
                    ("#%d" % r["Id"], 80, TX3),
                    ("机动模板", 130, TX2),
                    (ref_name(inst, "Mobility", r["MobilityId"]), 0, TXS),
                ],
                "own",
            )
            for r in props
        ],
    )
    yy = link_block(
        yy,
        "装甲归属（UnitArmors）",
        "2 行 · 必需 · 装甲模板 #1 被 4 个单位共用",
        [
            (
                [
                    ("#%d" % r["Id"], 80, TX3),
                    ("装甲套装", 130, TX2),
                    (
                        ref_name(inst, "Armors", r["ArmorId"]),
                        0,
                        ORANGE if r["ArmorId"] == 1 else TXS,
                    ),
                ],
                "own",
            )
            for r in armors
        ],
    )
    yy = link_block(
        yy,
        "传感器 / 能力 / 炮塔 / 运输（4 张表）",
        "共 12 行 · 独占 · 点表名可只看一张",
        [
            (
                [
                    ("传感器归属#%d" % r["Id"], 200, TX3),
                    ("传感器", 90, TX2),
                    (ref_name(inst, "Sensors", r["SensorId"]), 0, ORANGE),
                ],
                "own",
            )
            for r in sensors[:1]
        ]
        + [
            (
                [
                    ("能力归属#%d" % r["Id"], 200, TX3),
                    ("能力", 90, TX2),
                    (ref_name(inst, "Abilities", r["AbilityId"]), 0, TXS),
                ],
                "own",
            )
            for r in abils[:1]
        ]
        + [
            (
                [
                    ("炮塔归属#%d" % r["Id"], 200, TX3),
                    ("炮塔/挂架", 90, TX2),
                    (ref_name(inst, "Turrets", r["TurretId"]), 0, TXS),
                ],
                "own",
            )
            for r in turrets[:1]
        ],
        right="还有 9 行",
    )
    yy = link_block(
        yy,
        "武器携弹（WeaponAmmunitions）",
        "6 行 · 独占 · 点弹种进弹药编辑器",
        [
            (
                [
                    ("#%d" % r["Id"], 80, TX3),
                    (ref_name(inst, "Weapons", r["WeaponId"]), 250, TX2),
                    (ref_name(inst, "Ammunitions", r["AmmunitionId"]), 250, TX),
                    ("×%d" % r["Quantity"], 0, ORANGE),
                ],
                "own",
            )
            for r in sorted(ammos, key=lambda x: -x["Quantity"])[:2]
        ],
        right="还有 4 行",
    )
    yy = link_block(
        yy,
        "改装槽 → 改装选项（Modifications → Options）",
        "2 个槽 · 4 个选项 · 独占 · 点进 Options 专用页",
        [
            (
                [
                    ("改装槽#%d" % m["Id"], 150, TX3),
                    (m["Name"], 260, TX2),
                    (
                        "%d 个选项 →" % len(rows_where(inst, "Options", "ModificationId", m["Id"])),
                        0,
                        ACCENT,
                    ),
                ],
                "own",
            )
            for m in mods
        ],
    )
    yy = link_block(
        yy,
        "班组成员（SquadMembers）",
        "这个单位不是步兵 —— 没有这一行",
        [([("点「+ 加一行」可以给它配上", 400, TX3), ("", 0, TX3)], "own")],
    )
    return inner


# ---------------------------------------------------------------- 通用表单（中文名 + 解码值）
inst_holder: dict = {}


def form_panel(d, box, groups, *, per_row=2, cell_h=50, size=16):
    """groups: [(中文组名, 备注, [(表, 列, 值)])]。列名走 cn()，值走 decode()。"""
    x0, y0, x1, y1 = box
    y = y0
    for title, note, cells in groups:
        if y + 28 > y1:
            t(d, (x0 + 12, y), "…（滚动继续）", F(15), TX3)
            break
        y = group_header(d, (x0, y, x1, y1), title, note)
        fields = []
        for table, column, value in cells:
            sub = "%s · %s" % (TABLE_CN.get(table, table), column)
            node = enum_node(table, column)
            if node:
                text, kind = decode(table, column, value)
                kind = {"mask": "mask", "bool": "text"}.get(node.get("kind"), "enum")
                sub = "%s = %s · %s" % (column, fmt(value), node.get("doc", ""))
            elif column in REF_TABLE and isinstance(value, int):
                if value == 0:
                    text, kind = "（不使用）", "text"
                else:
                    target = REF_TABLE[column]
                    text, kind = ref_name(inst_holder["inst"], target, value), "ref"
                    sub = "%s#%s · %s" % (TABLE_CN.get(target, target), value, column)
            else:
                text, kind = fmt(value), "text"
            fields.append((cn(table, column), text, kind, sub))
        y = field_cells(d, (x0, y, x1, y1), fields, per_row=per_row, cell_h=cell_h)
        y += 6
    return y


def scrollbar(d, box):
    x0, y0, x1, y1 = box
    d.rounded_rectangle((x1 - 9, y0 + 6, x1 - 3, y1 - 6), radius=3, fill=PANEL)
    d.rounded_rectangle((x1 - 9, y0 + 6, x1 - 3, y0 + (y1 - y0) * 0.42), radius=3, fill=BORDER2)


def page_head(d, inner, crumbs, right="实例 52085 + 52082 同步 · 保存写 BA_Mod.zip"):
    ix0, iy0, ix1, _ = inner
    y = iy0 + 8
    t(d, (ix0 + 14, y + 1), "← 返回单位", F(17), ACCENT)
    crumb(d, ix0 + 140, y + 2, crumbs)
    t(d, (ix1 - 14, y + 2), right, F(15), TX3, anchor="ra")
    y += 30
    d.line((ix0, y, ix1, y), fill=BORDER)
    return y + 12


# ---------------------------------------------------------------- ③ 步兵班
def panel_squad(img, d, box, inst, unit_id):
    x0, y0, x1, y1 = box
    inner = window(img, d, x0, y0, x1 - x0, y1 - y0, "BA Mod Maker")
    ix0, iy0, ix1, iy1 = inner
    urow = row_of(inst, "Units", unit_id)
    uname = urow.get("HUDName") or ""
    y = page_head(d, inner, [("军械库", TX3, False), (uname, TXS, True), ("班组成员", TX3, False)])
    members = rows_where(inst, "SquadMembers", "UnitId", unit_id)
    members.sort(key=lambda r: r["Id"])
    t(d, (ix0 + 12, y), "一个班 = 一行一名士兵", F(20, bold=True), TX2)
    t(d, (ix0 + 300, y + 4), "加人点「+1」；减人直接点「✕」", F(16), TX3)
    t(d, (ix1 - 14, y + 4), "座位数 = %s" % urow.get("InfantrySlots"), F(15), TX3, anchor="ra")
    y += 30
    lw = 940
    for idx, m in enumerate(members):
        rr(d, (ix0 + 12, y, ix0 + 12 + lw, y + 48), 5, fill=ELEV, outline=BORDER)
        t(d, (ix0 + 24, y + 13), "第 %d 名" % (idx + 1), F(16), TX3)
        t(
            d,
            (ix0 + 110, y + 10),
            ref_name(inst, "Weapons", m["PrimaryWeaponId"]),
            F(19, bold=True),
            TXS,
        )
        sw = m["SpecialWeaponId"]
        t(
            d,
            (ix0 + 400, y + 13),
            ("副武器 " + ref_name(inst, "Weapons", sw)) if sw else "没有副武器",
            F(16),
            TX2,
        )
        t(d, (ix0 + 660, y + 13), m["ModelFileName"], F(15, latin=True), TX3)
        rr(
            d,
            (ix0 + 12 + lw - 44, y + 8, ix0 + 12 + lw - 8, y + 40),
            5,
            fill=DANGER_BG,
            outline=RED,
            width=1,
        )
        t(d, (ix0 + 12 + lw - 26, y + 12), "✕", F(19, latin=True, bold=True), RED, anchor="ma")
        y += 54
        if y > y0 + 560:
            t(d, (ix0 + 20, y), "…（还有 %d 名，滚动继续）" % (len(members) - idx - 1), F(15), TX3)
            y += 26
            break
    rr(d, (ix0 + 12, y, ix0 + 12 + 260, y + 46), 6, fill=SUCCESS_BG, outline=GREEN, width=2)
    t(d, (ix0 + 142, y + 11), "+1  加一名士兵", F(20, bold=True), GREEN, anchor="ma")
    t(d, (ix0 + 296, y + 5), "复制最后一行、给一个新 Id —— 卡片「成员数」+1、重量 +125", F(16), TX2)
    t(d, (ix0 + 296, y + 27), "（步兵重量 = 125 × 人数，不是 Units.Weight 那一列）", F(16), TX3)
    y += 60
    t(
        d,
        (ix0 + 12, y),
        "⚠ 步兵武器那条不变量：SquadMembers 用到的每一门枪，都必须在同单位的 "
        "班用武器里（单向包含）。违反会在立绘上冒感叹号。",
        F(16),
        TX3,
    )
    if members:
        rm = members[0]
        px = ix0 + 12 + lw + 24
        rr(d, (px, y0 + 110, ix1 - 12, y0 + 470), 8, fill=PANEL, outline=BLUE, width=2)
        t(d, (px + 16, y0 + 124), "选中第 1 名之后", F(19, bold=True), BLUE)
        groups = [
            (
                "这一名士兵",
                "班组成员#%d · 独占 · 改它只动这个兵" % rm["Id"],
                [
                    ("SquadMembers", "ModelFileName", rm["ModelFileName"]),
                    ("SquadMembers", "PrimaryWeaponId", rm["PrimaryWeaponId"]),
                    ("SquadMembers", "SpecialWeaponId", rm["SpecialWeaponId"] or 0),
                    ("SquadMembers", "DeathPriority", rm["DeathPriority"]),
                ],
            )
        ]
        form_panel(d, (px + 16, y0 + 158, ix1 - 28, y0 + 460), groups, per_row=2, cell_h=50)
        t(d, (px + 16, y0 + 430), "主武器 / 副武器都是外键 —— 按名字选，不填 Id。", F(14), TX3)
    return inner


# ---------------------------------------------------------------- ④ 武器
WEAPON_GROUPS = [
    (
        "身份",
        "武器模板表 · 只这一行",
        ["Id", "Name", "HUDName", "Type", "HUDIcon", "ModelFileName", "AudioPreset"],
    ),
    (
        "瞄准 · 稳定",
        "决定卡片上的「瞄准时间」",
        [
            "AimTimeMin",
            "AimTimeMax",
            "CanShootOnTheMove",
            "StabilizerQuality",
            "MultiTargetTracking",
            "SimultaneousTracking",
        ],
    ),
    (
        "射击",
        "弹匣 / 装填 / 点射 —— 喂给「射速」",
        [
            "MagazineSize",
            "MagazineReloadTimeMin",
            "MagazineReloadTimeMax",
            "ShotsPerBurstMin",
            "ShotsPerBurstMax",
            "TimeBetweenBurstsMin",
            "TimeBetweenBurstsMax",
            "TimeBetweenShotsInBurst",
        ],
    ),
    (
        "齐射",
        "只有榴弹炮 / 迫击炮 / 多管火箭炮会发射",
        ["ShortGroundAttackBurst", "NormalGroundAttackBurst", "LongGroundAttackBurst"],
    ),
    (
        "炮口指向",
        "俯仰角与转速",
        ["LowerVerticalAngle", "UpperVerticalAngles", "VerticalRotationSpeed"],
    ),
    (
        "标志位",
        "改这些会改行为，不只改显示",
        [
            "AutoLoaded",
            "Silent",
            "HasPriorityOnChassis",
            "CQC",
            "IsRadarDependent",
            "IsVerticalLauncher",
            "IsLowAltDirectWeapon",
            "IsHighAltDirectWeapon",
            "IsUnderbarrel",
            "CanBeMerged",
            "FlashPerShot",
        ],
    ),
]


def panel_weapon(img, d, box, inst, group):
    x0, y0, x1, y1 = box
    inner = window(img, d, x0, y0, x1 - x0, y1 - y0, "BA Mod Maker")
    ix0, iy0, ix1, iy1 = inner
    weapon_id = group.weapon_id
    wrow = row_of(inst, "Weapons", weapon_id)
    y = page_head(
        d,
        inner,
        [("军械库", TX3, False), ("BTR-82A", TXS, True), ("武器 " + group.label, TXS, True)],
    )
    lw = 620
    lx0, lx1 = ix0 + 12, ix0 + 12 + lw
    rr(d, (lx0, y, lx1, y + 170), 8, fill=PANEL, outline=BORDER)
    imw = icon("weapon_icons", group.icon)
    if imw is not None:
        paste_fit(img, imw, (lx0 + 20, y + 20, lx1 - 20, y + 110), tint=TXS)
    t(d, (lx0 + 20, y + 118), wrow.get("HUDName") or group.label, F(26, bold=True), TXS)
    t(d, (lx1 - 20, y + 124), "武器模板 #%d" % weapon_id, F(17), TX2, anchor="ra")
    t(d, (lx0 + 20, y + 146), "被 %d 个单位装 —— 改它会一起变" % group.shared_with, F(16), ORANGE)
    yy = y + 184
    t(d, (lx0, yy), "装在哪", F(19, bold=True), TX2)
    yy += 28
    for tt in (2, 364):
        rr(d, (lx0, yy, lx1, yy + 34), 5, fill=INPUT, outline=BORDER)
        t(d, (lx0 + 12, yy + 7), ref_name(inst, "Turrets", tt), F(17), TX)
        t(d, (lx1 - 12, yy + 9), "炮塔模板 #%d · 顺序 0" % tt, F(14), TX3, anchor="ra")
        yy += 40
    t(d, (lx0, yy), "这个单位能装的弹（点一行进弹药编辑器）", F(19, bold=True), TX2)
    yy += 28
    for a in group.ammo:
        rr(d, (lx0, yy, lx1, yy + 40), 5, fill=ELEV, outline=BORDER)
        ima = icon("ammo_icons", a.icon)
        if ima is not None:
            paste_fit(img, ima, (lx0 + 12, yy + 12, lx0 + 66, yy + 30), tint=TXS)
        t(d, (lx0 + 78, yy + 4), a.label, F(18, bold=True), TX)
        t(d, (lx0 + 78, yy + 24), "弹药模板 #%d" % a.ammo_id, F(14), TX3)
        t(d, (lx1 - 12, yy + 9), "×%s" % a.count, F(19, latin=True, bold=True), ORANGE, anchor="ra")
        yy += 46
    mount = next(
        (
            r
            for r in rows_where(inst, "TurretWeapons", "TurretId", 207)
            if r.get("WeaponId") == weapon_id
        ),
        {},
    )
    groups = [
        (t_, n_, [("Weapons", c, wrow.get(c)) for c in cols]) for t_, n_, cols in WEAPON_GROUPS
    ]
    groups.append(
        (
            "本次挂载的覆盖值（炮塔挂载表）",
            "按挂载点覆盖武器行同名值 —— 同一门枪挂在别处可以不同",
            [
                ("TurretWeapons", c, mount.get(c))
                for c in ("WeaponChannel", "WeaponPriority", "Order")
            ],
        )
    )
    rx0 = lx1 + 16
    t(d, (rx0, y - 2), "武器模板的全部词条", F(20, bold=True), TXS)
    t(
        d,
        (rx0 + d.textlength("武器模板的全部词条", font=F(20, bold=True)) + 12, y + 2),
        "武器模板表 40 列 · 中文名 + 悬浮注释",
        F(15),
        TX3,
    )
    form_panel(d, (rx0, y + 30, ix1 - 26, iy1 - 12), groups, per_row=2, cell_h=50)
    scrollbar(d, (ix1 - 30, y + 30, ix1 - 12, iy1 - 12))
    return inner


# ---------------------------------------------------------------- ⑤ 弹药
AMMO_GROUPS = [
    (
        "身份",
        "弹药模板表 · 只这一行",
        ["Id", "Name", "HUDName", "HUDIcon", "ModelFileName", "AudioPreset", "FXPreset"],
    ),
    (
        "弹道",
        "直射还是抛物线、飞多快",
        [
            "TrajectoryType",
            "MuzzleVelocity",
            "MaxSpeed",
            "Acceleration",
            "BurnTime",
            "LoftAngle",
            "LoftHeight",
        ],
    ),
    (
        "射程",
        "卡片上那几个 m 都 ×2",
        ["MinimalRange", "GroundRange", "LowAltRange", "HighAltRange", "OverflyHeight"],
    ),
    (
        "伤害 · 穿深",
        "卡片「伤害 / 穿深 / 爆炸半径」的原料",
        [
            "Damage",
            "StressDamage",
            "PenetrationAtMinRange",
            "PenetrationAtGroundRange",
            "ArmorTargeted",
            "HealthAOERadius",
            "StressAOERadius",
            "OverpressureRadius",
            "CriticMultiplier",
            "NoDamageFalloff",
            "TopArmorAttack",
        ],
    ),
    (
        "散布",
        "卡片「武器散布」那一行",
        [
            "DispersionHorizontalRadius",
            "DispersionVerticalRadius",
            "DispersionMinimal",
            "LaunchMarginAngle",
        ],
    ),
    (
        "制导 · 导引头",
        "只有导弹用得上",
        [
            "Seeker",
            "LaserGuided",
            "SeekerAngle",
            "MaxSeekerDistance",
            "CanReaquire",
            "AimStartDelay",
            "RotationSpeed",
        ],
    ),
    (
        "被打 / 防御",
        "「无视掩体」「可被 APS 拦截」在这一组",
        [
            "IgnoreCover",
            "CanBeIntercepted",
            "CanBeTargeted",
            "GenerateSmoke",
            "SmokeRadius",
            "SmokeDuration",
        ],
    ),
    ("能打什么", "位掩码 → 一串中文（不是数字）", ["TargetType", "UiOptions", "Airburst"]),
    (
        "补给",
        "卡片「补给值 / 补给时间 / 补给消耗」",
        ["SupplyCost", "ResupplyTime", "HUDMultiplier", "VfxCountPerShot", "RadioFuseDistance"],
    ),
]


def panel_ammo(img, d, box, inst, card, group, ammo):
    x0, y0, x1, y1 = box
    inner = window(img, d, x0, y0, x1 - x0, y1 - y0, "BA Mod Maker")
    ix0, iy0, ix1, iy1 = inner
    y = page_head(
        d,
        inner,
        [
            ("军械库", TX3, False),
            ("BTR-82A", TXS, True),
            ("武器 2А72 30mm", TXS, True),
            ("弹药 " + ammo.label, TXS, True),
        ],
    )
    arow = row_of(inst, "Ammunitions", ammo.ammo_id)
    lw = 620
    lx0, lx1 = ix0 + 12, ix0 + 12 + lw
    rr(d, (lx0, y, lx1, y + 150), 8, fill=PANEL, outline=BORDER)
    t(d, (lx0 + 20, y + 16), ammo.label, F(27, bold=True), TXS)
    t(d, (lx1 - 20, y + 22), "弹药模板 #%d" % ammo.ammo_id, F(17), TX2, anchor="ra")
    t(d, (lx0 + 20, y + 56), arow.get("Name") or "", F(16), TX2)
    t(
        d,
        (lx0 + 20, y + 86),
        "被 %d 个单位共用（这门弹还装在别处）" % ammo.shared_with,
        F(16),
        ORANGE,
    )
    t(
        d,
        (lx0 + 20, y + 114),
        "本单位的携弹量 ×%s —— 存在「武器携弹」表，不在弹药行上" % ammo.count,
        F(16),
        ACCENT,
    )
    yy = y + 164
    t(d, (lx0, yy), "能打什么（位掩码解出来的，点一下切换）", F(19, bold=True), TX2)
    yy += 28
    names = {
        "infantry": "步兵",
        "vehicle": "载具",
        "helicopter": "直升机",
        "aircraft": "飞机",
        "missile": "导弹",
    }
    raw = arow.get("TargetType") or 0
    node = enum_node("Ammunitions", "TargetType")
    vals = node.get("values", {})
    on = set()
    for k, label in vals.items():
        try:
            bit = int(k)
        except ValueError:
            continue
        if raw & bit:
            on.add(label)
    cx = lx0
    for _key, ccn in names.items():
        w = d.textlength(ccn, font=F(17)) + 30
        sel = ccn in on
        rr(
            d,
            (cx, yy, cx + w, yy + 32),
            5,
            fill=SELECTED if sel else INPUT,
            outline=ACCENT if sel else BORDER,
        )
        t(d, (cx + 15, yy + 6), ccn, F(17, bold=sel), TXS if sel else TX3)
        cx += w + 8
    yy += 42
    t(
        d,
        (lx0, yy),
        "原始值 TargetType = %s → 解出来「%s」"
        % (raw, decode("Ammunitions", "TargetType", raw)[0]),
        F(15),
        PURPLE,
    )
    yy += 28
    t(d, (lx0, yy), "同武器下的其它弹（换一个点进去）", F(19, bold=True), TX2)
    yy += 26
    for other, ccn in ((207, "30x165mm HE"), (208, "30x165mm AP")):
        rr(
            d,
            (lx0, yy, lx1, yy + 34),
            5,
            fill=SELECTED if other == ammo.ammo_id else ELEV,
            outline=ACCENT if other == ammo.ammo_id else BORDER,
        )
        t(
            d,
            (lx0 + 12, yy + 7),
            ccn,
            F(17, bold=other == ammo.ammo_id),
            TX if other == ammo.ammo_id else TX2,
        )
        t(d, (lx1 - 12, yy + 9), "弹药模板 #%d" % other, F(14), TX3, anchor="ra")
        yy += 40
    rx0 = lx1 + 16
    t(d, (rx0, y - 2), "弹药模板的全部词条", F(20, bold=True), TXS)
    t(
        d,
        (rx0 + d.textlength("弹药模板的全部词条", font=F(20, bold=True)) + 12, y + 2),
        "弹药模板表 %d 列 —— 信息量最大的一张" % len(inst.table("Ammunitions").columns),
        F(15),
        TX3,
    )
    groups = [
        (t_, n_, [("Ammunitions", c, arow.get(c)) for c in cols]) for t_, n_, cols in AMMO_GROUPS
    ]
    form_panel(d, (rx0, y + 30, ix1 - 26, iy1 - 12), groups, per_row=2, cell_h=50)
    scrollbar(d, (ix1 - 30, y + 30, ix1 - 12, iy1 - 12))
    return inner


# ---------------------------------------------------------------- ⑥ Options
OPTION_GROUPS = [
    (
        "身份",
        "改装选项表 · 只这一行",
        ["Id", "ModificationId", "Name", "UIName", "Order", "IsDefault"],
    ),
    (
        "显示",
        "选这一项时卡片上换什么图 / 什么名",
        [
            "OptionPicture",
            "ThumbnailOverride",
            "PortraitOverride",
            "AudioPresetOverride",
            "ReplaceUnitName",
            "ConcatenateWithUnitName",
        ],
    ),
    (
        "成本与时间",
        "卡片成本 = 单位基础价 + 默认项费用之和",
        ["Cost", "TimeModifierRefund", "TimeModifierResurrect"],
    ),
    ("覆盖 · 装甲与机动", "非 0 = 把单位本体那一维整套换掉", ["ArmorId", "MobilityId"]),
    ("覆盖 · 传感器", "主传感器是整套替换，不是追加", ["MainSensorId", "ExtraSensorId"]),
    ("覆盖 · 能力", "最多三项", ["Ability1Id", "Ability2Id", "Ability3Id"]),
    (
        "覆盖 · 隐蔽与整单位替换",
        "ReplaceUnitId 非 0 = 这一项是「换成另一个单位」",
        ["StealthOverride", "ReplaceUnitId", "IsDynamicTurretOption"],
    ),
]
OPTION_TURRET_COLS = ["Turret%dId" % i for i in range(21)]


def panel_options(img, d, box, inst, card, opt_id):
    x0, y0, x1, y1 = box
    inner = window(img, d, x0, y0, x1 - x0, y1 - y0, "BA Mod Maker")
    ix0, iy0, ix1, iy1 = inner
    orow = row_of(inst, "Options", opt_id)
    y = page_head(
        d,
        inner,
        [
            ("军械库", TX3, False),
            ("BTR-82A", TXS, True),
            ("改装槽 " + ref_name(inst, "Modifications", orow.get("ModificationId")), TXS, True),
        ],
    )
    lw = 620
    lx0, lx1 = ix0 + 12, ix0 + 12 + lw
    rr(d, (lx0, y, lx1, y + 170), 8, fill=PANEL, outline=BORDER)
    imp = icon("option_pictures", orow.get("OptionPicture") or "")
    if imp is not None:
        paste_fit(img, imp, (lx0 + 20, y + 24, lx0 + 150, y + 130), tint=TXS)
    t(d, (lx0 + 170, y + 30), str(orow.get("UIName")), F(26, bold=True), TXS)
    t(d, (lx1 - 20, y + 36), "选项 #%d" % opt_id, F(17), TX2, anchor="ra")
    t(
        d,
        (lx0 + 170, y + 70),
        "改装槽：%s" % ref_name(inst, "Modifications", orow.get("ModificationId")),
        F(16),
        TX2,
    )
    t(d, (lx0 + 170, y + 98), "改装件费用 %s" % orow.get("Cost"), F(16), ORANGE)
    t(d, (lx0 + 170, y + 126), "卡片成本 = 单位基础价 55 + 默认项费用之和", F(15), TX3)
    yy = y + 184
    t(d, (lx0, yy), "同一个槽里的其它选项（点一下换过去）", F(19, bold=True), TX2)
    yy += 28
    for o in rows_where(inst, "Options", "ModificationId", orow.get("ModificationId")):
        sel = o["Id"] == opt_id
        rr(
            d,
            (lx0, yy, lx1, yy + 40),
            5,
            fill=SELECTED if sel else ELEV,
            outline=ACCENT if sel else BORDER,
        )
        iop = icon("option_pictures", o.get("OptionPicture") or "")
        if iop is not None:
            paste_fit(img, iop, (lx0 + 10, yy + 8, lx0 + 62, yy + 34), tint=TXS)
        t(d, (lx0 + 74, yy + 9), str(o.get("UIName")), F(17, bold=sel), TX if sel else TX2)
        t(d, (lx1 - 12, yy + 10), "费用 %s" % o.get("Cost"), F(15), TX3, anchor="ra")
        yy += 46
    t(d, (lx0, yy + 6), "★ 这就是「换装改变单位」的唯一入口：这些覆盖列一旦非 0，", F(16), ACCENT)
    t(
        d,
        (lx0, yy + 30),
        "   卡片上的成本 / 武器 / 数值 / 名字 / 缩略图 / 装甲 六条链都要跟着重算。",
        F(16),
        ACCENT,
    )
    rx0 = lx1 + 16
    t(d, (rx0, y - 2), "改装选项的全部词条", F(20, bold=True), TXS)
    t(
        d,
        (rx0 + d.textlength("改装选项的全部词条", font=F(20, bold=True)) + 12, y + 2),
        "改装选项表 46 列 —— 其中 21 列是炮塔挂载位",
        F(15),
        TX3,
    )
    groups = [
        (t_, n_, [("Options", c, orow.get(c)) for c in cols]) for t_, n_, cols in OPTION_GROUPS
    ]
    groups.append(
        (
            "覆盖 · 炮塔挂载（21 个槽）",
            "Turret0..19 是「这个选项要装的炮塔列表」，Turret20 全 0（保留）",
            [("Options", c, orow.get(c)) for c in OPTION_TURRET_COLS],
        )
    )
    form_panel(d, (rx0, y + 30, ix1 - 26, iy1 - 12), groups, per_row=3, cell_h=46)
    scrollbar(d, (ix1 - 30, y + 30, ix1 - 12, iy1 - 12))
    return inner


# ------------------------------------------------------------- 校验（真跑规则书里的全部规则）
import re as _re  # noqa: E402

# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")


def humanize(inst, message: str) -> str:
    """把提示文案里的裸 Id 换成名字 —— 用户口径：别让人去查 Id。"""

    def sub(m):
        try:
            return ref_name(inst, "Units", int(m.group(1)))
        except (TypeError, ValueError):
            return m.group(0)

    return _re.sub(r"Id (\d+)", sub, message)


def unit_hits(inst, unit_id: int):
    """跑规则引擎，返回 (打开时就有几条, 模拟改坏之后新增几条, 新增的那几条)。

    口径（产品已定的方案乙）：**原版既有违规不报、不参与计数**。
    真改一格再看新增 —— 这里改的是内存里的 Units#1.InfantrySlots，改完立刻改回来；
    磁盘上的 DataBaseCompiled 一个字节不动。
    """
    try:
        from ba_mod_maker.core.rules.attribution import findings_by_unit
        from ba_mod_maker.core.rules.engine import RuleEngine
    except Exception:
        return (0, 0, [])
    engine = RuleEngine()
    before = findings_by_unit(inst, engine.validate(inst)).get(unit_id, ())
    table = inst.table("Units")
    index = table.columns.index("Id")
    row = next((r for r in table.rows if r[index] == unit_id), None)
    if row is None:
        return (len(before), 0, [])
    col = table.columns.index("InfantrySlots")
    keep = row[col]
    row[col] = 2  # 故意改坏：装不下一个班
    after = findings_by_unit(inst, engine.validate(inst)).get(unit_id, ())
    row[col] = keep  # 改回来，磁盘没碰
    fresh = [f for f in after if f not in before]
    return (len(before), len(fresh), fresh)


def build_hints(inst, card):
    """★ 立绘上只贴**校验**（用户口径：共享与说明那两类不要）。

    返回 (刚打开时的提示, 改坏之后的提示)。
    **没有任何校验问题就是空列表 —— 立绘上一个字都不画。**
    """
    _n_before, n_fresh, fresh = unit_hits(inst, 1)
    if not n_fresh:
        return [], []
    return [], [("bad", "校验 %d 处" % n_fresh, humanize(inst, fresh[0].message))]


# ---------------------------------------------------------------- 图 A · 「点开一格」
def strip_edit(img, d, box, card):
    x0, y0, x1, y1 = box
    rr(d, box, 8, fill=PANEL, outline=ACCENT, width=2)
    t(
        d,
        (x0 + 20, y0 + 14),
        "点开一格：就地输入 · 落点写清楚 · 共享只在这里说",
        F(26, bold=True),
        ACCENT,
    )
    t(
        d,
        (x0 + 20, y0 + 48),
        "立绘上不提示共享；真要改到别人的行时，在这一步说 —— 因为这一步才有「另存为新模板」这个动作。",
        F(16),
        TX3,
    )
    top = y0 + 84
    # 左：卡片上那一格被点开
    t(d, (x0 + 20, top), "① 点卡片上的「生命值」那一格", F(17, bold=True), TX2)
    st = next((s for s in card.stats if s.key == "health"), card.stats[0])
    rr(d, (x0 + 20, top + 28, x0 + 20 + 260, top + 96), 6, fill=ORANGE_BG, outline=ORANGE, width=2)
    ims = icon("infocard_stats", st.sprite)
    if ims is not None:
        paste_fit(img, ims, (x0 + 30, top + 34, x0 + 60, top + 56), tint=TXS)
    t(d, (x0 + 150, top + 34), st.value, F(26, latin=True, bold=True), TXS, anchor="ma")
    t(d, (x0 + 150, top + 68), "生命值 —— 橙色 = 这一行还被别人用着", F(14), TX2, anchor="ma")
    d.line((x0 + 290, top + 62, x0 + 350, top + 62), fill=ACCENT, width=3)
    d.polygon([(x0 + 344, top + 55), (x0 + 344, top + 69), (x0 + 356, top + 62)], fill=ACCENT)
    # 右：就地编辑条
    ex0 = x0 + 380
    rr(d, (ex0, top + 20, ex0 + 1010, top + 190), 8, fill=ELEV, outline=ACCENT, width=2)
    t(d, (ex0 + 18, top + 32), "生命值", F(20, bold=True), TXS)
    t(d, (ex0 + 110, top + 34), st.value, F(18), TX2)
    t(d, (ex0 + 156, top + 34), "→", F(18), TX3)
    rr(d, (ex0 + 186, top + 28, ex0 + 326, top + 66), 6, fill=INPUT, outline=ACCENT, width=2)
    t(d, (ex0 + 200, top + 32), "20", F(24, latin=True, bold=True), TXS)
    d.line((ex0 + 224, top + 34, ex0 + 224, top + 60), fill=ACCENT, width=2)
    t(d, (ex0 + 990, top + 36), "✓", F(22, bold=True), GREEN, anchor="ra")
    t(d, (ex0 + 950, top + 36), "✕", F(20), TX3, anchor="ra")
    d.line((ex0 + 16, top + 76, ex0 + 994, top + 76), fill=BORDER, width=1)
    rows = [
        ("落在哪", "装甲模板 #1 · MaxHealthPoints", BLUE),
        ("共享", "这一行还被 4 个单位用着 —— 直接改会一起变", ORANGE),
        ("备注", "被选项 #504 的「覆盖·装甲」指着", TX2),
    ]
    ry = top + 86
    for label, value, color in rows:
        t(d, (ex0 + 18, ry), label, F(16), TX3)
        t(d, (ex0 + 100, ry), value, F(16), color)
        ry += 24
    bxx = ex0 + 100
    for label, kind in (("另存为新模板", "primary"), ("4 个一起改", "ghost")):
        w = d.textlength(label, font=F(16, bold=True)) + 26
        rr(
            d,
            (bxx, ry + 2, bxx + w, ry + 30),
            5,
            fill=ACCENT if kind == "primary" else ELEV,
            outline=None if kind == "primary" else BORDER,
        )
        t(
            d,
            (bxx + w // 2, ry + 7),
            label,
            F(16, bold=True),
            (255, 255, 255) if kind == "primary" else TX,
            anchor="ma",
        )
        bxx += w + 10
    t(d, (ex0 + 994, ry + 8), "Esc 取消 · Enter 确认", F(14), TX3, anchor="ra")
    # 右侧注解
    nx = ex0 + 1040
    rr(d, (nx, top + 20, x1 - 20, top + 190), 8, fill=ELEV, outline=BORDER, width=1)
    t(d, (nx + 16, top + 32), "这一条为什么不放在立绘上", F(18, bold=True), TX2)
    notes = [
        "立绘是「一眼看过去」的地方 —— 常驻的共享/说明会把它糊住。",
        "共享只在**真要改到它**的那一刻才有意义：",
        "所以它出现在输入框旁边，和「另存为新模板」这个动作挨着。",
        "不做这一步的后果是静默的：改一次生命值会连带另外 4 个单位。",
    ]
    ry = top + 62
    for line in notes:
        t(d, (nx + 16, ry), line, F(15), TX3)
        ry += 24


# ---------------------------------------------------------------- 图 A · 细节条
def strip_details(img, d, box, inst, card, hints_clean, hints_dirty):
    x0, y0, x1, y1 = box
    rr(d, box, 8, fill=PANEL, outline=BORDER)
    t(d, (x0 + 20, y0 + 14), "四条细节", F(26, bold=True), TX2)
    t(d, (x0 + 160, y0 + 20), "提示贴立绘 · 外键按名字 · 枚举解成中文 · 词条带注释", F(18), TX3)
    top = y0 + 56
    w = (x1 - x0 - 40 - 3 * 16) // 4

    b = (x0 + 20, top, x0 + 20 + w, y1 - 20)
    rr(d, b, 6, fill=ELEV, outline=RED, width=2)
    t(d, (b[0] + 16, top + 10), "① 立绘上只挂一个校验角标", F(20, bold=True), RED)
    t(
        d,
        (b[0] + 16, top + 38),
        "共享与说明那两类提示已去掉。校验没问题时，立绘上一个字都不画。",
        F(15),
        TX2,
    )
    half = (b[2] - b[0] - 40) // 2
    portrait = icon("unit_portraits", card.portrait_key) or Image.new("RGBA", (1, 1))
    # 两张立绘并排（同一张，只是状态不同）
    for i, (label, col) in enumerate((("A 改坏之后", RED), ("B 刚打开", GREEN))):
        px = b[0] + 16 + i * (half + 8)
        rr(d, (px, top + 66, px + half, top + 200), 6, fill=INPUT, outline=col, width=1)
        t(d, (px + 12, top + 72), label, F(15, bold=True), col)
        paste_fit(img, portrait, (px + 12, top + 94, px + half - 12, top + 194))
    # A：角标（整块宽度，文案放得下）
    hint_chips(d, (b[0] + 16, top + 208, b[2] - 16, top + 254), hints_dirty)
    t(d, (b[0] + 16, top + 260), "A 把座位数 8 改成 2 之后 —— 立绘右下角出现这一条", F(14), RED)
    # B：什么都不画
    rr(
        d,
        (b[0] + 16, top + 284, b[2] - 16, top + 330),
        6,
        fill=(11, 14, 18, 235),
        outline=BORDER2,
        width=1,
    )
    t(
        d,
        ((b[0] + b[2]) // 2, top + 298),
        "B 刚打开：原版既有违规不报、不计数 —— 这里什么都不画",
        F(16),
        TX3,
        anchor="ma",
    )
    t(d, (b[0] + 16, top + 340), "角标点一下就滚到出问题的那一条词条。", F(14), TX3)
    t(
        d,
        (b[0] + 16, top + 362),
        "共享 / 说明那两类提示已去掉 —— 共享只在「点开那一格」时出现（见上一条）。",
        F(14),
        TX3,
    )

    b2 = (b[2] + 16, top, b[2] + 16 + w, y1 - 20)
    rr(d, b2, 6, fill=ELEV, outline=ACCENT, width=2)
    t(d, (b2[0] + 16, top + 12), "② 外键按 Name / HUDName 输入", F(20, bold=True), ACCENT)
    t(
        d,
        (b2[0] + 16, top + 42),
        "不用再查 Id —— 打几个字就筛出来，Name 与 HUDName 都认。",
        F(15),
        TX2,
    )
    ref_picker(
        d,
        (b2[0] + 16, top + 70, b2[2] - 16, top + 320),
        "机动模板（机动归属 → MobilityId）",
        "wheels btr",
        [
            ("Wheels BTR82A Base", "机动模板 #89", "现在用的"),
            ("Wheels BTR82AT", "机动模板 #305", ""),
            ("Wheels BTR80 Base", "机动模板 #76", ""),
        ],
        "输入的是名字，写回去的是 Id。找不到就明说「没找到」，不瞎猜。",
    )
    t(
        d,
        (b2[0] + 16, top + 334),
        "同理：装甲套装 / 传感器 / 炮塔 / 武器 / 弹种 / 阵营 / 专精 / 改装槽 —— 全部按名字。",
        F(14),
        TX3,
    )

    b3 = (b2[2] + 16, top, b2[2] + 16 + w, y1 - 20)
    rr(d, b3, 6, fill=ELEV, outline=PURPLE, width=2)
    t(d, (b3[0] + 16, top + 12), "③ 枚举 / 掩码解成中文", F(20, bold=True), PURPLE)
    t(d, (b3[0] + 16, top + 42), "数据库里是数字，界面上是中文；旁边留原始值备查。", F(15), TX2)
    ry = top + 74
    for table, col, raw in (
        ("Ammunitions", "TrajectoryType", 10),
        ("Ammunitions", "TargetType", 36),
        ("Weapons", "Type", 114),
        ("Units", "Type", 4),
        ("Units", "CategoryType", 2),
        ("Ammunitions", "Seeker", 0),
    ):
        text, kind = decode(table, col, raw)
        rr(d, (b3[0] + 16, ry, b3[2] - 16, ry + 46), 4, fill=INPUT)
        t(d, (b3[0] + 28, ry + 5), cn(table, col), F(15), TX3)
        t(d, (b3[0] + 28, ry + 25), "%s.%s" % (table, col), F(13, latin=True), TX3)
        color = PURPLE if kind == "mask" else BLUE
        t(
            d,
            (b3[2] - 28, ry + 8),
            cut(d, text, F(17, bold=True), 320),
            F(17, bold=True),
            color,
            anchor="ra",
        )
        t(d, (b3[2] - 28, ry + 28), "原始值 %s" % raw, F(13, latin=True), TX3, anchor="ra")
        ry += 52
    t(
        d,
        (b3[0] + 16, top + 402),
        "这张对照表由队友在铺：枚举逐档、掩码逐位，每条都要有出处。",
        F(14),
        TX3,
    )

    b4 = (b3[2] + 16, top, b3[2] + 16 + w, y1 - 20)
    rr(d, b4, 6, fill=ELEV, outline=GREEN, width=2)
    t(d, (b4[0] + 16, top + 12), "④ 每一条都有中文名 + 悬浮注释", F(20, bold=True), GREEN)
    t(
        d,
        (b4[0] + 16, top + 42),
        "注释说的是「它管什么、单位是什么、谁在读它」，不是同义反复。",
        F(15),
        TX2,
    )
    rows = [
        ("复活时间修正", "单位本体#1"),
        ("地面光学", "观瞄模板#2"),
        ("无视掩体", "弹药模板#208"),
        ("携弹量", "武器携弹#905"),
    ]
    ry = top + 74
    for ccn, where in rows:
        rr(d, (b4[0] + 16, ry, b4[2] - 16, ry + 38), 4, fill=INPUT)
        t(d, (b4[0] + 28, ry + 8), ccn, F(17, bold=True), TX)
        t(d, (b4[2] - 28, ry + 10), where, F(14), TX3, anchor="ra")
        ry += 44
    tooltip(
        img,
        d,
        (b4[0] + 16, ry + 6, b4[2] - 16, ry + 156),
        [
            ("Units.TimeModifierResurrect", TX3),
            ("复活时间修正（秒）", TXS),
            ("阵亡后的整备时间 = 1.5 × Cost + 420 + 本列。", TX),
            ("BTR-82A 的 Cost 是 55、本列是 −120，所以卡片显示 6:22。", TX2),
            ("全库 177 行是 −120、177 行是 0。", TX3),
        ],
        accent=GREEN,
        title="复活时间修正",
    )
    return


# ---------------------------------------------------------------- 主图
def main() -> int:
    n_tables, n_cells = load_dicts()
    print("词典：field_labels.json 给了 %d 张表，field_enums.json 给了 %d 列" % (n_tables, n_cells))
    inst, data, card = load_all()
    inst_holder["inst"] = inst
    units = wall_units(data, 56)
    one = next((u for u in units if u[0] == 1), None)
    if one is None:
        from ba_mod_maker.app.viewmodel.unit_card import card_cost

        one = (1, "BTR-82A", "RU_BTR82-Label", int(card_cost(data, 1)))
    units.remove(one)
    units.insert(0, one)
    group = card.weapons[0]
    ammo = group.ammo[1] if len(group.ammo) > 1 else group.ammo[0]
    hints_clean, hints_dirty = build_hints(inst, card)
    squad_unit = 645

    width = 3400
    # ============================================================ 图 A · 总览
    img = Image.new("RGBA", (width, 2600), BG + (255,))
    d = ImageDraw.Draw(img)
    y = 28
    t(d, (40, y), "设计稿 v4 · 双击军械库上的一个单位 → 单位编辑器", F(54, bold=True), TXS)
    y += 66
    t(
        d,
        (40, y),
        "双击之后不再显示军械库的单位列表；卡片仍是编辑器，右边是「这个单位的全部词条」"
        "（中文名 + 悬浮注释 + 外键写名字）。立绘上只挂一个校验角标，没问题就不画。"
        "（数字全部真机数据：BTR-82A，Id 1）",
        F(21),
        TX2,
    )
    y += 44
    d.line((40, y, width - 40, y), fill=BORDER2, width=2)
    y += 22
    chips = [
        ("① 步兵班 +1 / ✕", GREEN),
        ("② Options 专用页", GREEN),
        ("③ 校验 = 立绘上一个感叹号", RED),
        ("④ 外键按名字输入", ACCENT),
        ("⑤ 枚举 / 掩码解成中文", PURPLE),
        ("⑥ 词条全中文 + 注释", BLUE),
    ]
    cx = 40
    for text, col in chips:
        w = d.textlength(text, font=F(20, bold=True)) + 34
        rr(d, (cx, y, cx + w, y + 42), 6, fill=ELEV, outline=col, width=2)
        t(d, (cx + 17, y + 10), text, F(20, bold=True), col)
        cx += w + 12
    y += 66
    h1 = 1160
    caption(d, 40, y - 2, "① 军械库（现状）", "单击选中 · 双击进入", TX2)
    caption(
        d,
        960,
        y - 2,
        "② 单位编辑器（双击之后）",
        "左边没有单位列表；卡片上的立绘驮着全部提示；右栏是全部词条",
        BLUE,
    )
    panel_armory(img, d, (40, y + 24, 940, y + 24 + h1), units, card)
    panel_unit_editor(img, d, (960, y + 24, width - 40, y + 24 + h1), inst, card, hints_clean)
    y += 24 + h1 + 34
    strip_edit(img, d, (40, y, width - 40, y + 300), card)
    y += 300 + 26
    strip_details(img, d, (40, y, width - 40, y + 480), inst, card, hints_clean, hints_dirty)
    y += 480 + 26
    t(
        d,
        (40, y),
        "生成器：tools/gen_unit_editor_mock.py —— 卡片、字段值、校验命中全部现读；"
        "词条中文名与枚举对照优先取队友产出（task-1 枚举解码 / task-2 中文名+注释）。状态：设计稿，未实现。",
        F(17),
        TX3,
    )
    y += 40
    img = img.convert("RGB").crop((0, 0, width, y))
    img.save(DOCS / "设计稿-单位编辑器.png", "PNG")
    print("写出 docs/设计稿-单位编辑器.png", img.size)

    # ============================================================ 图 B · 四个专用页
    img2 = Image.new("RGBA", (width, 2200), BG + (255,))
    d2 = ImageDraw.Draw(img2)
    y = 28
    t(d2, (40, y), "四个专用页 · 信息量大的那几张表各给一页", F(52, bold=True), TXS)
    y += 64
    t(
        d2,
        (40, y),
        "步兵班（+1 加人 / ✕ 减人）· 武器（40 列）· 弹药（67 列）· 改装选项（46 列，含 21 个炮塔挂载位）。"
        "每一页都是「左边这一行的上下文 + 右边全部词条」同一个版式。",
        F(21),
        TX2,
    )
    y += 44
    d2.line((40, y, width - 40, y), fill=BORDER2, width=2)
    y += 22
    hh, ww = 880, 1650
    caption(d2, 40, y, "③ 步兵班", "一个班 = 一行一名士兵；加人点 +1，减人点 ✕", GREEN)
    caption(d2, 1740, y, "④ 武器", "Weapons 40 列 + 本次挂载的覆盖值", BLUE)
    panel_squad(img2, d2, (40, y + 24, 40 + ww, y + 24 + hh), inst, squad_unit)
    panel_weapon(img2, d2, (1740, y + 24, width - 40, y + 24 + hh), inst, group)
    y += 24 + hh + 30
    caption(d2, 40, y, "⑤ 弹药", "Ammunitions 67 列 —— 信息量最大的一张", ORANGE)
    caption(d2, 1740, y, "⑥ 改装选项", "Options 46 列，其中 21 列是炮塔挂载位", PURPLE)
    panel_ammo(img2, d2, (40, y + 24, 40 + ww, y + 24 + hh), inst, card, group, ammo)
    panel_options(img2, d2, (1740, y + 24, width - 40, y + 24 + hh), inst, card, 504)
    y += 24 + hh + 30
    d2.line((40, y, width - 40, y), fill=BORDER2, width=2)
    y += 16
    t(
        d2,
        (40, y),
        "「+ 加一行 / ✕ 删这一行」在四个页里是同一套：加 = 复制一行、给个新 Id、把关联行指过去；"
        "删 = 删掉这一行，并检查有没有别人还在引用它。",
        F(17),
        TX3,
    )
    y += 40
    img2 = img2.convert("RGB").crop((0, 0, width, y))
    img2.save(DOCS / "设计稿-专用页.png", "PNG")
    print("写出 docs/设计稿-专用页.png", img2.size)

    # ============================================================ 单件
    def save_page(path, maker, size):
        solo = Image.new("RGBA", size, BG + (255,))
        sd = ImageDraw.Draw(solo)
        maker(solo, sd, size)
        solo.convert("RGB").save(path, "PNG")
        print("写出", path)

    save_page(
        DOCS / "设计稿-步兵班编辑器.png",
        lambda im, dd, s: panel_squad(im, dd, (0, 0, s[0], s[1]), inst, squad_unit),
        (1700, 940),
    )
    save_page(
        DOCS / "设计稿-武器编辑器.png",
        lambda im, dd, s: panel_weapon(im, dd, (0, 0, s[0], s[1]), inst, group),
        (1700, 940),
    )
    save_page(
        DOCS / "设计稿-弹药编辑器.png",
        lambda im, dd, s: panel_ammo(im, dd, (0, 0, s[0], s[1]), inst, card, group, ammo),
        (1700, 940),
    )
    save_page(
        DOCS / "设计稿-Options编辑器.png",
        lambda im, dd, s: panel_options(im, dd, (0, 0, s[0], s[1]), inst, card, 504),
        (1700, 940),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
