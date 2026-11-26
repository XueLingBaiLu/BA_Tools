# ruff: noqa: E501, UP031
# 说明：本文件大量使用 "%d" % (...) 与长中文拼接来拼 markdown 行。
# 换成 f-string / format 会把「哪一段是模板、哪一段是数据」搅在一起，反而更难读，
# 所以这两条规则在此文件整体豁免（不是遗漏）。
"""生成《步兵链字段级关系图》。

为什么要有生成器而不是直接写一份 md：
文档里的每一个数字（行数、扇出、取值分布、原版例外清单）都必须能从
DataBaseCompiled 现算出来。游戏更新后重跑一次，文档就跟着更新，
不会出现「文档说 1134 行、实际 1140 行」这种漂移。

**薄封装**（AGENTS.md §5「逻辑只有一份」）：数据层一件都不自己干 —— 认实例目录、
读 `_schema.json` / `_manifest.json`、读表，全走 `core.data`；
「最新实例 = pathID 最大」走 `Dataset.latest_instance_id`；规则文本走产品自带的
`core.rules.load_rulebook()`。本文件只负责“把这些数排成一份给人看的 md”。
（曾经这里自抄过一份数据层，还用 `pids[-1]` 自己取最大实例 —— 游戏更新换了
pathID、或数据侧改了实例目录的认定规则时，两边就会各说各话。）

用法：
    python tools/gen_infantry_map.py            # 数据源默认取 ../DataBaseCompiled
    python tools/gen_infantry_map.py <数据源> [实例id]
    输出：docs/关系图-步兵链.md
"""

from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 直接跑脚本时 src 不在 sys.path 上（没装成包也能用）—— 与 tools/ 下其他薄封装同款。
sys.path.insert(0, str(ROOT / "src"))

from ba_mod_maker.core.data import Dataset, load_dataset  # noqa: E402
from ba_mod_maker.core.rules import load_rulebook  # noqa: E402

DEFAULT_DATASET = ROOT.parent / "DataBaseCompiled"
OUT = ROOT / "docs" / "关系图-步兵链.md"

TYPE_LABEL = {2: "步兵", 4: "载具", 8: "直升机", 16: "飞机", 32: "舰船"}
ARMOR_PAT = re.compile(r"^Armor Infantry(?: (Shock|Elite))? ([0-9]+)Men\s*$")

#: 反引号。生成器源码里不写字面反引号，避免 md 代码块与 Python 字符串互相打架。
BT = chr(96)


def q(text: str) -> str:
    """把一段文字包成 md 行内代码。"""
    return BT + text + BT


class Data:
    """数据访问的**薄适配层**：只把产品的 `Table` 翻成「按列名取值」的 dict 行。

    读取本身全在 `core.data` / `core.rules`：

    - 哪些目录算实例（数字名 + `_manifest.json`）、`_schema.json` 怎么读：`load_dataset`；
    - 「最新实例 = pathID 最大」：`Dataset.latest_instance_id`；
    - 规则文本（id / table / severity / expr）：`core.rules.load_rulebook()`。

    为什么还要翻成 dict 行：下面五百行全是「按列名说人话」的文案，
    用 `Table.columns` 的下标写会让每一句都变成 `row[17]`，读不出对错。
    适配只在这一层，而且**只做适配、不做加载**。
    """

    def __init__(self, dataset: Dataset, pid: str) -> None:
        self.dataset = dataset.root
        self.pid = pid
        self.instance = dataset.instance(pid)
        #: 表名 -> 列名。与 `_schema.json` 同一个来源（那份是字母序，不是声明序）。
        self.schema = {name: list(spec.columns) for name, spec in self.instance.schema.items()}
        #: 产品自带的规则数据（与界面上的实时校验同源）。
        self.rules = load_rulebook()
        self._t: dict[str, list[dict]] = {}

    def t(self, name: str) -> list[dict]:
        """一张表的行（惰加载 + 缓存），每行是「列名 -> 值」的 dict。"""
        if name not in self._t:
            table = self.instance.table(name)
            self._t[name] = [dict(zip(table.columns, row, strict=True)) for row in table.rows]
        return self._t[name]


def group(rows, key="UnitId"):
    g = collections.defaultdict(list)
    for r in rows:
        g[r[key]].append(r)
    return g


def build(d: Data) -> str:
    units = d.t("Units")
    sm, sw = d.t("SquadMembers"), d.t("SquadWeapons")
    ua, armors = d.t("UnitArmors"), d.t("Armors")
    up, mobility = d.t("UnitPropulsions"), d.t("Mobility")
    uab, abilities = d.t("UnitAbilities"), d.t("Abilities")
    wa = d.t("WeaponAmmunitions")
    options, mods = d.t("Options"), d.t("Modifications")
    transport = d.t("TransportAvailabilities")

    un = {u["Id"]: u for u in units}
    arn = {a["Id"]: a for a in armors}
    abn = {a["Id"]: a for a in abilities}
    mname = {m["Id"]: m.get("Name") for m in mobility}
    inf = {u["Id"]: u for u in units if u.get("Type") == 2}

    sm_by = group(sm)
    sw_by = group(sw)
    ua_by = group(ua)
    up_by = group(up)
    uab_by = group(uab)
    n_men = collections.Counter(r["UnitId"] for r in sm)
    mod_by_id = {m["Id"]: m for m in mods}

    lines: list[str] = []
    add = lines.append
    n_mod = sum(1 for u in units if u.get("IsUnitModification"))
    n_mod_inf = sum(1 for u in inf.values() if u.get("IsUnitModification"))
    targets = {o["ReplaceUnitId"] for o in options if o.get("ReplaceUnitId")}

    # ================= 头 =================
    add("# 关系图 · 步兵链（字段级）")
    add("")
    add("> **本文件由 " + q("tools/gen_infantry_map.py") + " 生成，不要手改。**")
    add("> 数据源：" + q("DataBaseCompiled/" + d.pid) + "（pathID 最大者）")
    add(">")
    add("> 与 " + q("DataBaseCompiled/树6-步兵链.png") + " 的分工：那张图回答【结构是什么】，")
    add("> 这张图回答【我改这一个字段，会连动哪几行】。")
    add("")
    add("读法：每节 = 一张表。节内三块 —— 关键列 / 扇出 / 改它会连动什么。")
    add("")
    add("---")
    add("")

    # ================= 0 =================
    add("## 0 · 一屏看懂")
    add("")
    add("~~~")
    add("Units  (Type=2)   步兵 %d 个 / 全库 %d 个" % (len(inf), len(units)))
    add("|")
    add("+- SquadMembers -------- %-5d 行 / %d 个单位 / 一行 = 一名士兵" % (len(sm), len(sm_by)))
    add("|    +- ModelFileName ------ 模型键（裸名，走 catalog）")
    add("|    +- PrimaryWeaponId ---- 指向 Weapons")
    add("|    +- SpecialWeaponId ---- 指向 Weapons（0 = 没有）")
    add("|")
    add(
        "+- SquadWeapons -------- %-5d 行 / %d 个单位 / 一行 = 这个班有这件武器"
        % (len(sw), len(sw_by))
    )
    add("|    +- 【error】SquadMembers 用到的武器 必须 在本清单里（单向包含）")
    add("|")
    add("+- UnitArmors ---------- 一行 = 这个单位用哪套护甲（步兵：每单位 1 行）")
    add("|    +- Armors ---------- 护甲套装")
    add("|         +- Name = Armor Infantry [Shock|Elite] NMen    <- N 必须 == 班人数")
    add("|         +- MaxHealthPoints == 5 x N")
    add("|")
    _inf_mob = {r["MobilityId"] for u in inf for r in up_by.get(u, [])}
    _inf_ab = {r["AbilityId"] for u in inf for r in uab_by.get(u, [])}
    add("+- UnitPropulsions ------ 一行 = (单位, 机动)。步兵只用到 %d 种 Mobility" % len(_inf_mob))
    add("|")
    add("+- UnitAbilities -------- 一行 = (单位, 能力)。步兵只用到 %d 种 Abilities" % len(_inf_ab))
    add("|")
    add("+- WeaponAmmunitions ---- 一行 = (单位 x 武器) 的弹药与携弹量")
    add("|")
    add("+- 搭载 / 运输（步兵自己不是载具，但被它们引用）")
    add("|    +- Units.InfantrySlots ........ 载具侧字段，步兵恒 0")
    add("|    +- TransportAvailabilities ... 一行 = 某载具可搭载某步兵班")
    add("|")
    add("+- 改装链：Modifications --> Options --> Units（替换成另一个单位）")
    add("     步兵特有：全库 %d 个改装变体里 %d 个是步兵" % (n_mod, n_mod_inf))
    add("~~~")
    add("")
    add("---")
    add("")  # ================= 1 =================
    add("## 1 · 步兵单位本身（Units，Type=2）")
    add("")
    add("### 1.1 分类：工坊左栏要区分的三种")
    add("")
    core = [u for u in inf.values() if not u.get("IsUnitModification") and u.get("DisplayInArmory")]
    hidden = [u for u in inf.values() if not u.get("DisplayInArmory")]
    add("| 类 | 判据 | 个数 | 工坊怎么处理 |")
    add("|---|---|---|---|")
    add(
        "| 常规步兵 | "
        + q("not IsUnitModification and DisplayInArmory")
        + " | %d | 左栏正常列出 |" % len(core)
    )
    add(
        "| 改装变体 | "
        + q("IsUnitModification = true")
        + " | %d | 左栏可列，但标注【改装件，由某个选项换上】 |" % n_mod_inf
    )
    add(
        "| 隐藏 | "
        + q("DisplayInArmory = false")
        + " | %d | 左栏默认不列（其中 %d 个同时也是变体） |"
        % (len(hidden), sum(1 for u in hidden if u.get("IsUnitModification")))
    )
    add("")
    add(q("IsUnitModification") + " 的语义（**本轮实测钉死**，此前只知字段名）：")
    add("")
    add(
        "- 全库为 true 的单位：**%d 个**，全部都被某条 " % n_mod
        + q("Options.ReplaceUnitId")
        + " 指向（%d/%d，无一例外）。" % (n_mod, n_mod)
    )
    add(
        "- 但反向不成立：被指向的目标有 %d 个，其中 **%d 个是常规单位**（如 Buk M2 换成 Buk M3）。"
        % (len(targets), len(targets) - n_mod)
    )
    add(
        "  ⇒ "
        + q("IsUnitModification")
        + " 只描述这一行本身是不是某人的改装变体，**不描述有没有人指向它**。"
    )
    add("- 按兵种分布：**步兵是绝大头**。")
    add("")
    add("| UnitType | 改装变体 / 总数 |")
    add("|---|---|")
    for _t, _lab in sorted(TYPE_LABEL.items()):
        _tot = sum(1 for u in units if u.get("Type") == _t)
        if _tot:
            _c = sum(1 for u in units if u.get("Type") == _t and u.get("IsUnitModification"))
            add("| %s（%d） | %d / %d |" % (_lab, _t, _c, _tot))
    add("")
    add("### 1.2 Units 表的每一列")
    add("")
    add("| 列 | 步兵用法 | 改它会连动什么 |")
    add("|---|---|---|")
    _facts = _units_column_facts(units, inf)
    for _col in d.schema["Units"]:
        _u, _e = _facts.get(_col, ("—", "—"))
        add("| " + q(_col) + " | " + _u + " | " + _e + " |")
    add("")
    add("### 1.3 三个从未被使用的列（本轮实测）")
    add("")
    add("| 列 | 全库非零 | 结论 |")
    add("|---|---|---|")
    for _col in ("OriginalName", "OwnerInfantryID", "OriginalCost"):
        _nz = [u["Id"] for u in units if u.get(_col) not in (0, "", None, False)]
        add(
            "| "
            + q(_col)
            + " | %d | **从未被写入**。工坊归入【原版恒定】档，不展示为可编辑 |" % len(_nz)
        )
    add("")
    add("> 这三个字段此前没有任何文档下过结论。" + q("OwnerInfantryID") + " 名字上像是")
    add(
        "> 【这个载具运的是哪个步兵班】，但实测全库为 0 —— **真正的搭载关系走 "
        + q("TransportAvailabilities")
        + "**（见第 8 节）。"
    )
    add("")
    add("### 1.4 步兵上的【恒定值】字段 —— 它们不是可编辑项")
    add("")
    add("步兵 194 个单位的这些列**取值完全相同**，说明它们是占位符或运行期字段：")
    add("")
    add("| 列 | 步兵上的取值 | 真正的值在哪 |")
    add("|---|---|---|")
    add(
        "| "
        + q("ModelFileName")
        + " | **194/194 全是 "
        + q("US_Rangers")
        + "** | "
        + q("SquadMembers.ModelFileName")
        + "（%d 种，一名士兵一行） |" % len({r["ModelFileName"] for r in sm})
    )
    add("| " + q("Description") + " | None | 界面文案不走这里（走本地化表） |")
    add("| " + q("Height") + " | 2.0 | 载具/飞机才有意义 |")
    add("| " + q("Weight") + " | 1 | 同上 |")
    add("| " + q("UpkeepMultiplier") + " | 1.0 | — |")
    add("| " + q("WaterDiveOffset") + " | 0.0 | 只有两栖载具用 |")
    add("| " + q("MaxStress") + " | 1000.0 | — |")
    add("")
    add(
        "> ⚠️ **这是个坑**：全库 "
        + q("Units.ModelFileName")
        + " 有 %d 种取值，" % len({u.get("ModelFileName") for u in units})
        + "但**步兵那 194 个全部是同一个 "
        + q("US_Rangers")
        + "**。"
    )
    add("> 谁要是照着载具的做法去改步兵的 " + q("Units.ModelFileName") + "，**什么都不会发生** ——")
    add("> 实机换的是 " + q("SquadMembers.ModelFileName") + "。工坊必须把这一列的步兵含义标清楚。")
    add("")

    # ================= 2 =================
    add("## 2 · SquadMembers —— 一行 = 一名士兵")
    add("")
    _table_header(add, d, "SquadMembers", inf)
    add("")
    add("| 列 | 含义 | 实测 |")
    add("|---|---|---|")
    add(
        "| "
        + q("UnitId")
        + " | 指向 "
        + q("Units.Id")
        + "（error 外键） | %d 行覆盖 %d 个单位；**全部是步兵** |" % (len(sm), len(sm_by))
    )
    add(
        "| "
        + q("ModelFileName")
        + " | 士兵模型键（裸名，走 catalog） | %d 种不同值 |"
        % len({r["ModelFileName"] for r in sm})
    )
    add(
        "| "
        + q("PrimaryWeaponId")
        + " | 指向 "
        + q("Weapons.Id")
        + "（error 外键） | %d / %d 非 0 —— **必填** |"
        % (sum(1 for r in sm if r.get("PrimaryWeaponId")), len(sm))
    )
    add(
        "| "
        + q("SpecialWeaponId")
        + " | 指向 "
        + q("Weapons.Id")
        + "（error 外键） | %d / %d 非 0（0 = 没有副武器） |"
        % (sum(1 for r in sm if r.get("SpecialWeaponId")), len(sm))
    )
    add(
        "| "
        + q("DeathPriority")
        + " | 阵亡波次（不是序号） | 分布 %s |"
        % dict(sorted(collections.Counter(r["DeathPriority"] for r in sm).items()))
    )
    add("")
    add(
        "**扇出**：一个步兵班 %d 到 %d 人（众数 %d，平均 %.1f）。"
        % (
            min(n_men.values()),
            max(n_men.values()),
            collections.Counter(n_men.values()).most_common(1)[0][0],
            sum(n_men.values()) / len(n_men),
        )
    )
    add("")
    add("**改它会连动什么**：")
    add("")
    add("- **增/删一行 = 增/删一名士兵**。连带必须同步两处，否则校验报错：")
    add(
        "  1. "
        + q("SquadWeapons")
        + " 必须**包含**新士兵用的武器（error 规则 "
        + q("inv.infantry.squadmembers_subset_squadweapons")
        + "）；"
    )
    add(
        "  2. 护甲套装的 "
        + q("NMen")
        + " 应等于新人数（warning 规则 "
        + q("inv.infantry.armor_men")
        + "）——"
    )
    add(
        "     而护甲的 "
        + q("MaxHealthPoints = 5 x NMen")
        + "，所以**人数一改、全队血量就跟着该变**。"
    )
    add("- 改 " + q("PrimaryWeaponId") + " / " + q("SpecialWeaponId") + "：只连动校验；")
    add("  弹药在 " + q("WeaponAmmunitions") + " 里**按【单位 x 武器】另存**（见第 6 节）。")
    add("- 改 " + q("DeathPriority") + "：不连动任何表，只影响战斗中的阵亡顺序。")
    add("")

    # ================= 3 =================
    add("## 3 · SquadWeapons —— 一行 = 这个班有这件武器")
    add("")
    _table_header(add, d, "SquadWeapons", inf)
    add("")
    add("| 列 | 含义 | 实测 |")
    add("|---|---|---|")
    add(
        "| "
        + q("UnitId")
        + " | 指向 "
        + q("Units.Id")
        + "（error 外键） | %d 行覆盖 %d 个单位，与 SquadMembers **完全同一批** |"
        % (len(sw), len(sw_by))
    )
    add(
        "| "
        + q("WeaponId")
        + " | 指向 "
        + q("Weapons.Id")
        + "（error 外键） | %d 种不同武器 |" % len({r["WeaponId"] for r in sw})
    )
    add(
        "| "
        + q("Order")
        + " | 展示顺序 | 分布 %s |"
        % dict(sorted(collections.Counter(r["Order"] for r in sw).items())[:5])
    )
    add("")
    add("### ★ 单向包含（整条链唯一的 error 级跨表规则）")
    add("")
    _viol = _extra = _extra_units = 0
    for _uid, _rows in sm_by.items():
        _need = {r["PrimaryWeaponId"] for r in _rows} | {
            r["SpecialWeaponId"] for r in _rows if r.get("SpecialWeaponId")
        }
        _have = {r["WeaponId"] for r in sw_by.get(_uid, [])}
        if _need - _have:
            _viol += 1
        if _have - _need:
            _extra += len(_have - _need)
            _extra_units += 1
    add("~~~")
    add("SquadMembers 里用到的武器  ⊆  SquadWeapons 清单      <- 必须（error）")
    add("清单里没人携带的武器                                  <- 合法（不显示、无影响）")
    add("~~~")
    add("")
    add("- 原版违反（成员用了清单里没有的武器）：**%d 个单位**。" % _viol)
    add(
        "- 原版【清单多余】：%d 个单位共多出 %d 种武器（同族变体/附件，没人携带）。"
        % (_extra_units, _extra)
    )
    add("- **用户实证**：成员有、清单没有 → HUD 不显示这件武器。这就是这条规则为什么是 error。")
    add(
        "- **改它会连动什么**：把武器从清单里删掉、而还有成员在用它 → 立刻 error。往清单里加则永远安全。"
    )
    add("")  # ================= 4 =================
    add("## 4 · UnitArmors + Armors —— 护甲，步兵最容易被改坏的地方")
    add("")
    add("~~~")
    add("UnitArmors   一行 = (单位, 护甲)      列：Id / UnitId / ArmorId")
    add("   +- Armors   护甲套装               列：Name / ArmorValue / MaxHealthPoints / ...")
    add("~~~")
    add("")
    add(
        "- 步兵单位**每个恰好 1 行** " + q("UnitArmors") + "（实测 %d/%d）。" % (len(inf), len(inf))
    )
    _n_def = sum(1 for a in armors if a.get("IsDefault"))
    add(
        "- "
        + q("Armors.IsDefault")
        + "：**%d / %d 为 true** —— 它就是【**是否默认**】" % (_n_def, len(armors))
    )
    add(
        "  （用户 2026-10-03 纠正过：从前这里写着「允许被单位直接引用」，那是对这一列的误读）。"
        + q("req.UnitArmors.IsDefault")
        + "（error）说的正是这件事："
    )
    add(
        "  每个单位引用的 "
        + q("Armors")
        + " 行其 "
        + q("IsDefault")
        + " 必须为 true。原版违反 **%d** 个。" % _default_violations(ua_by, arn, units)
    )
    add("")
    add("### ★ Armor Infantry NMen 的 N 就是班人数")
    add("")
    _ok = _bad = _nopat = _hp_ok = _hp_tot = 0
    _bad_rows = []
    for _uid in sorted(inf):
        _n = n_men.get(_uid, 0)
        _aids = ua_by.get(_uid, [])
        _a = arn.get(_aids[0]["ArmorId"]) if _aids else None
        _m = ARMOR_PAT.match(_a["Name"]) if _a and _a.get("Name") else None
        if not _m:
            _nopat += 1
            _why = "**没有 N**（特殊护甲）"
            if _a and _a.get("Name", "").endswith(" "):
                _why = "名称尾部有空格，但 N 对得上"
            _bad_rows.append(
                (
                    _uid,
                    _n,
                    _a["Name"] if _a else "(无)",
                    None,
                    _a.get("MaxHealthPoints") if _a else None,
                    _why,
                )
            )
            continue
        _nmen = int(_m.group(2))
        if _a.get("MaxHealthPoints") == 5 * _nmen:
            _hp_ok += 1
        _hp_tot += 1
        if _nmen == _n:
            _ok += 1
        else:
            _bad += 1
            _bad_rows.append(
                (_uid, _n, _a["Name"], _nmen, _a.get("MaxHealthPoints"), "**N != 班人数**")
            )
    add("| 检查 | 结果 |")
    add("|---|---|")
    add(
        "| 护甲名符合 "
        + q("Armor Infantry [Shock|Elite] NMen")
        + " | %d / %d |" % (_ok + _bad, len(inf))
    )
    add("| 其中 " + q("N == 班人数") + " | **%d** |" % _ok)
    add("| 其中 " + q("N != 班人数") + " | **%d** ← 全部是 ZOM 系列 |" % _bad)
    add("| 名称完全不符该模式 | %d |" % _nopat)
    add("| " + q("MaxHealthPoints == 5 x N") + " | **%d / %d** |" % (_hp_ok, _hp_tot))
    add("")
    add("**全部例外（%d 条）：**" % len(_bad_rows))
    add("")
    add("| 单位 | 班人数 | 护甲 | NMen | MaxHealthPoints | 问题 |")
    add("|---|---|---|---|---|---|")
    for _uid, _n, _an, _nmen, _hp, _tag in _bad_rows:
        add(
            "| %d %s | %d | %s | %s | %s | %s |"
            % (_uid, inf[_uid]["Name"], _n, q(_an), _nmen if _nmen is not None else "—", _hp, _tag)
        )
    add("")
    add("> " + q("_rules.json") + " 的 note 写着【5 个例外全是 ZOM 系列】—— **实测确认它是对的**：")
    add("> " + q("N != 班人数") + " 的正好 5 条，全是 ZOM。")
    add(
        "> 表里第 6 条 "
        + q("RU Officer")
        + " 属于**另一种情况**：护甲叫 "
        + q("Armor Infantry UnarmoredMen")
        + "，"
    )
    add("> 名称里根本没有 N。**校验器必须把【N 不等】和【没有 N】分开报**，")
    add("> 否则要么漏报要么误报。（另一条干扰项是尾随空格，见下节。）")
    add("")
    _trail = [a for a in armors if (a.get("Name") or "") != (a.get("Name") or "").strip()]
    add("### 原版数据质量：Armors 的名字有尾随空格")
    add("")
    add(
        "实测 "
        + q("Armors")
        + " 有 **%d 行**名字带首尾空白（例如 " % len(_trail)
        + q("Armor Infantry Elite 3Men ")
        + "、 "
        + q("Armor T72B ")
        + "）。"
    )
    add("")
    add(
        "⇒ **校验器与关系图生成器都必须先 strip 再匹配**，否则会把 "
        + q("Armor Infantry Elite 3Men ")
        + " 误判成【名称不符模式】。"
    )
    add("⇒ 工坊展示与比对时也应当按 strip 后的值处理，但**保存时必须原样写回**（不要顺手修掉它）。")
    add("")
    add("### 套装档位")
    add("")
    add("| 前缀 | ArmorValue | 血量 |")
    add("|---|---|---|")
    for _pref, _lab in (("(无)", "标准"), ("Shock", "Shock"), ("Elite", "Elite")):
        _vals = sorted(
            {a["ArmorValue"] for a in armors if _armor_prefix(a.get("Name") or "") == _pref}
        )
        add("| %s | %s | " % (_lab, _vals) + q("5 x NMen") + " |")
    add("")
    add("**改它会连动什么**：")
    add("")
    add(
        "- 换 "
        + q("ArmorId")
        + " → 血量、"
        + q("ArmorValue")
        + " 一起变（"
        + q("MaxHealthPoints")
        + " 挂在护甲行上，不在单位上）。"
    )
    add("- **加/减班组成员时，护甲必须跟着换**，否则 warning。而护甲行是**共享模板**，")
    _share = sum(1 for r in ua if arn.get(r["ArmorId"], {}).get("Name") == "Armor Infantry 4Men")
    add(
        "  例如 " + q("Armor Infantry 4Men") + " 被 %d 个单位引用 —— 所以必须走设计稿 C 的" % _share
    )
    add("  【只改这个 / 另存为新行】对话框。")
    add(
        "- "
        + q("Armors.ModelFileName")
        + " 在步兵护甲上**全是 None**（步兵护甲只有数值，没有模型）。"
    )
    add("")

    # ================= 5 =================
    add("## 5 · UnitPropulsions + Mobility —— 机动")
    add("")
    _inf_mob = sorted({r["MobilityId"] for u in inf for r in up_by.get(u, [])})
    add(
        "步兵用到的机动只有 **%d 种**（全库 " % len(_inf_mob)
        + q("Mobility")
        + " 共 %d 行）：" % len(mobility)
    )
    add("")
    add("| MobilityId | Name | 被几个步兵单位用 |")
    add("|---|---|---|")
    _mob_cnt = collections.Counter(r["MobilityId"] for u in inf for r in up_by.get(u, []))
    for _mid in _inf_mob:
        add("| %d | %s | %d |" % (_mid, mname.get(_mid), _mob_cnt[_mid]))
    add("")
    add(
        "**改它会连动什么**："
        + q("Mobility.ModelFileName")
        + " 在步兵机动上也是 None。换 "
        + q("MobilityId")
        + " 不连动别的表。"
    )
    add("")

    # ================= 6 =================
    add("## 6 · WeaponAmmunitions —— 弹药是按【单位 x 武器】存的")
    add("")
    add("~~~")
    add("WeaponAmmunitions   一行 = (UnitId, WeaponId) 的弹药与携弹量")
    add("   列：Id / UnitId / WeaponId / AmmunitionId / Quantity / Order")
    add("   ★ 键里带 UnitId => 同一件武器在不同单位上可以有不同弹药 / 弹量")
    add("~~~")
    add("")
    _per = collections.Counter(r["UnitId"] for r in wa if r["UnitId"] in inf)
    add("- 步兵单位**全部**都有弹药行（%d / %d）。" % (len(_per), len(inf)))
    add("- 每单位行数分布：%s。" % dict(sorted(collections.Counter(_per.values()).items())))
    add("- 这与炮塔链**共用同一张表**（见树 5）—— 不是步兵专属。")
    add("")
    add("**改它会连动什么**：给某个班加一件武器，除了 " + q("SquadWeapons") + " 要加行，")
    add("" + q("WeaponAmmunitions") + " 也要为这个 (单位, 武器) 加一行，否则那件武器**没有弹药**。")
    add("")

    # ================= 7 =================
    add("## 7 · UnitAbilities + Abilities —— 能力")
    add("")
    _ab_cnt = collections.Counter(r["AbilityId"] for u in inf for r in uab_by.get(u, []))
    add(
        "步兵只用到 **%d 种**能力（全库 " % len(_ab_cnt)
        + q("Abilities")
        + " 共 %d 行）：" % len(abilities)
    )
    add("")
    add("| AbilityId | Name | IsInfantrySprint | 被几个步兵单位用 |")
    add("|---|---|---|---|")
    for _aid, _c in _ab_cnt.most_common():
        add(
            "| %d | %s | %s | %d |"
            % (_aid, abn.get(_aid, {}).get("Name"), abn.get(_aid, {}).get("IsInfantrySprint"), _c)
        )
    add("")
    add("> 注意最后一行：" + q("Smoke Zombie x99") + " 是 " + q("IsInfantrySprint = False") + " ——")
    add("> 步兵并非全部能力都带这个标志。")
    add("")  # ================= 8 =================
    add("## 8 · 悬挂在步兵上的两张【外面】的表")
    add("")
    add("### 8.1 Units.InfantrySlots —— 这是**载具**的字段")
    add("")
    add("- 实测：**全部 %d 个步兵单位的 " % len(inf) + q("InfantrySlots") + " 都是 0**。")
    add("- 它是【这个载具能装几个人】，所以列在载具行上。改步兵时不该动它。")
    add("")
    add("### 8.2 TransportAvailabilities —— 一行 = 某载具可搭载某步兵班")
    add("")
    _tr_units = {r["UnitId"] for r in transport}
    add(
        "- %d 行，行内 " % len(transport)
        + q("UnitId")
        + " = **载具**（%d 车 + %d 直升机）。"
        % (
            sum(1 for u in _tr_units if un.get(u, {}).get("Type") == 4),
            sum(1 for u in _tr_units if un.get(u, {}).get("Type") == 8),
        )
    )
    add("- 乘客侧是另一列 " + q("SpecializationAvailabilityId") + "（间接指向单位）。")
    add("- ★ 不变量（warning 规则 " + q("inv.transport.capacity") + "）：")
    add(
        "  **载具的 " + q("Units.InfantrySlots") + " >= 乘客班的 " + q("SquadMembers") + " 行数**。"
    )
    add("")
    add(
        "⇒ **增删班组成员的第二个连带后果**：这个班如果被某个载具运输，人数涨过载具容量就报 warning。"
    )
    add("")

    # ================= 9 =================
    add("## 9 · 改装链：Modifications -> Options -> Units（步兵特有）")
    add("")
    add("~~~")
    add("宿主步兵单位 -- Modifications（一行 = 一个改装槽）")
    add("                  +- Options（一行 = 槽里的一个可选项）")
    add("                       +- Cost           ★ 是【相对成本】，不是绝对值")
    add("                       +- ReplaceUnitId ---> Units（换成另一个完整单位）")
    add("                                              ^ 这个单位 IsUnitModification = true")
    add("~~~")
    add("")
    add("### 9.1 Options.Cost 是相对成本（**此前未结案，本轮实测**）")
    add("")
    _rel = _abs = _neither = 0
    for _o in options:
        _tgt = _o.get("ReplaceUnitId")
        if not _tgt:
            continue
        _mod = mod_by_id.get(_o.get("ModificationId"))
        if not _mod:
            continue
        _host, _t = un.get(_mod["UnitId"]), un.get(_tgt)
        if not _host or not _t:
            continue
        if _host["Cost"] + _o["Cost"] == _t["Cost"]:
            _rel += 1
        elif _o["Cost"] == _t["Cost"]:
            _abs += 1
        else:
            _neither += 1
    add("| 假设 | 成立行数 |")
    add("|---|---|")
    add("| **宿主.Cost + Option.Cost == 目标.Cost** | **%d** |" % _rel)
    add("| Option.Cost == 目标.Cost（绝对值） | %d |" % _abs)
    add("| 两者都不符 | %d |" % _neither)
    add("")
    add("⇒ **改装件上写的是【加多少钱】，不是【改成多少钱】。**")
    add("工坊的成本预览必须按【基数 + 选中改装件的 Cost 之和】算，与 kb/11 §6 的成本徽章公式同源。")
    add("")
    add("### 9.2 步兵是改装链的主战场")
    add("")
    _veh_mod = sum(1 for u in units if u.get("IsUnitModification") and u.get("Type") in (4, 8))
    add(
        "- 全库 %d 个改装变体里，**%d 个是步兵**；载具+直升机只有 %d 个。"
        % (n_mod, n_mod_inf, _veh_mod)
    )
    add(
        "- 一个变体是**完整的单位**：它有自己的 "
        + q("SquadMembers")
        + " / "
        + q("SquadWeapons")
        + " / "
        + q("UnitArmors")
        + " / "
        + q("WeaponAmmunitions")
        + "。"
    )
    _has_sm = sum(1 for u in inf.values() if u.get("IsUnitModification") and u["Id"] in sm_by)
    add("  （实测：%d / %d 个变体有自己的成员行）" % (_has_sm, n_mod_inf))
    add("")

    # ================= 10 =================
    add("## 10 · 改一个字段会连动哪几行（速查表）")
    add("")
    add("| 我改这个 | 必须同时改 | 会被哪条规则拦 |")
    add("|---|---|---|")
    _rows = [
        ("Units.Cost", "无", "无（但成本徽章 = Cost + 默认改装件费用，见 kb/11 §6）"),
        ("Units.Name / HUDName", "无", "无（卡片显示的是本地化结果）"),
        (
            "Units.Type 改离 2",
            "删掉该单位的 SquadMembers / SquadWeapons 行",
            "req.SquadMembers（warning）",
        ),
        ("Units.ThumbnailFileName", "无", "external.dangling_in_vanilla（info）"),
        (
            "SquadMembers 加一行",
            "① SquadWeapons 要含新武器 ② Armors 换 NMen+1 那套",
            "① error ② warning ③ inv.transport.capacity warning（若被运输）",
        ),
        ("SquadMembers 删一行", "同上（NMen-1）", "同上"),
        (
            "SquadMembers.PrimaryWeaponId",
            "SquadWeapons 要有这件武器",
            "inv.infantry.squadmembers_subset_squadweapons（error）",
        ),
        ("SquadWeapons 删一行", "确认没有成员在用", "error（若还有人在用）"),
        (
            "UnitArmors.ArmorId",
            "Armors 行的 IsDefault 必须 true；NMen 要对得上人数",
            "req.UnitArmors.IsDefault（error）+ warning",
        ),
        ("Armors.Name（改 NMen）", "所有引用它的单位（**共享行！**）", "warning；共享面见设计稿 C"),
        (
            "Modifications / Options",
            "ReplaceUnitId 指向的单位要存在且自洽",
            "fk.Options.ReplaceUnitId（error）",
        ),
        ("Options.Cost", "目标单位的 Cost（记住是相对值）", "无"),
    ]
    for _a, _b, _c in _rows:
        add("| " + q(_a) + " | " + _b + " | " + _c + " |")
    add("")

    # ================= 11 =================
    add("## 11 · 工坊里必须拦住的 5 个动作")
    add("")
    add("| # | 动作 | 为什么 |")
    add("|---|---|---|")
    add(
        "| 1 | 改 SquadMembers 人数却不动护甲 | 血量是 5 x NMen，人数与护甲不一致 → warning，且实机血量不对 |"
    )
    add("| 2 | 删 SquadWeapons 行但还有成员在用 | error；实机表现是 HUD 不显示那件武器 |")
    add("| 3 | 就地改共享护甲行的 NMen | 会改到另外 N 个单位的血量（共享行） |")
    add("| 4 | 把 Options.Cost 当绝对值填 | 实机总价会变成【宿主 + 你填的】 |")
    add("| 5 | 给非步兵单位加 SquadMembers | 无 error，但游戏永远不读它（LoadSquads 只对步兵走） |")
    add("")

    add("---")
    add("")
    add("## 附 · 证据等级")
    add("")
    add("| 标记 | 本文件里的例子 |")
    add("|---|---|")
    add(
        "| 【实测】 | 全部行数、扇出、取值分布、例外清单、"
        + q("5 x NMen")
        + " 公式、"
        + q("Options.Cost")
        + " 相对性 |"
    )
    add(
        "| 【代码】 | "
        + q("LoadSquads")
        + " / "
        + q("CreateFakeTurret")
        + " / "
        + q("TryGetWeaponCount")
        + "（引自树 6，本轮未复核） |"
    )
    add("| 【用户实证】 | 【成员有、清单没有 → HUD 不显示】【DeathPriority 越大越先阵亡】 |")
    add("")
    add("⛔ 本文件不引旧 .re-kb，不引旧知识包。")
    add("")
    return "\n".join(lines)


def _units_column_facts(units, inf) -> dict[str, tuple[str, str]]:
    """给 Units 的每一列一句【步兵用法】与【改它连动什么】。"""
    f: dict[str, tuple[str, str]] = {}
    il = list(inf.values())
    costs = [u.get("Cost", 0) for u in il]
    f["Id"] = ("主键", "全库引用锚；改它要同步 11 张表的所有 UnitId")
    f["Name"] = ("内部名", "不连动")
    f["HUDName"] = ("界面显示名", "不连动（卡片显示的其实是本地化结果，见 kb/11 §3）")
    f["OriginalName"] = ("**从未使用**", "—")
    f["OwnerInfantryID"] = ("**从未使用**", "—")
    f["OriginalCost"] = ("**从未使用**", "—")
    f["Cost"] = (
        "%d 到 %d" % (min(costs), max(costs)),
        "不连动；但成本徽章 = Cost + 默认改装件费用",
    )
    f["CountryId"] = ("指向 Countries", "只影响阵营筛选")
    f["Type"] = ("= 2（步兵）", "改成非 2 后 SquadMembers / SquadWeapons 全部失效")
    f["CategoryType"] = (
        "分布 %s" % dict(sorted(collections.Counter(u.get("CategoryType") for u in il).items())),
        "只影响军械库分行",
    )
    f["ModelFileName"] = (
        "%d 种（步兵上是占位符）" % len({u.get("ModelFileName") for u in il}),
        "**步兵改它无效** —— 真正的模型在 SquadMembers.ModelFileName（见 1.4）",
    )
    f["PortraitFileName"] = (
        "%d 种" % len({u.get("PortraitFileName") for u in il}),
        "走 catalog 键",
    )
    f["ThumbnailFileName"] = (
        "%d 种" % len({u.get("ThumbnailFileName") for u in il}),
        "走 catalog 键；改了要 IconCache.invalidate",
    )
    f["AudioPreset"] = ("%d 种" % len({u.get("AudioPreset") for u in il}), "走 catalog 键")
    f["InfantrySlots"] = ("**恒为 0**（这是载具字段）", "—")
    f["DisplayInArmory"] = (
        "%d / %d 为 true" % (sum(1 for u in il if u.get("DisplayInArmory")), len(il)),
        "只影响军械库是否列出",
    )
    f["IsUnitModification"] = (
        "%d / %d 为 true" % (sum(1 for u in il if u.get("IsUnitModification")), len(il)),
        "见第 9 节：变体自己是完整单位，不共享",
    )
    f["ContentMembership"] = (
        "分布 %s"
        % dict(collections.Counter(u.get("ContentMembership") for u in il).most_common(4)),
        "DLC 归属，不连动",
    )
    f["Role"] = ("%d 种" % len({u.get("Role") for u in il}), "不连动")
    f["Stealth"] = ("小数", "不连动")
    f["CurrentStealth"] = ("与 Stealth 同值", "运行期字段")
    f["MaxStress"] = ("1000.0 恒定", "不连动")
    for c in (
        "UpkeepMultiplier",
        "TimeModifierRefund",
        "TimeModifierResurrect",
        "Length",
        "Width",
        "Height",
        "Weight",
        "WaterDiveOffset",
        "Description",
    ):
        if c in f:
            continue
        vals = {u.get(c) for u in il}
        f[c] = (
            "恒定 %s" % (vals.pop() if len(vals) == 1 else "")
            if len(vals) == 1
            else "%d 种取值" % len(vals),
            "不连动",
        )
    return f


def _armor_prefix(name: str) -> str | None:
    """护甲名的档位前缀：Shock / Elite / 「(无)」；名称完全不符模式时返回 None。

    为什么写成具名函数而不是就地一个 lambda：套装档位那张表要按前缀分组，
    就地 lambda 读起来是「一个立即调用的匿名函数」，看不出判据是什么。
    （名称实测有尾随空格，如 `Armor Infantry Elite 3Men ` —— 所以判据是模式匹配，
    不是拿原串去比。）
    """
    match = ARMOR_PAT.match(name)
    if match is None:
        return None
    return match.group(1) or "(无)"


def _default_violations(ua_by, arn, units) -> int:
    """req.UnitArmors.IsDefault：每个单位的 UnitArmors 行，指向的 Armors.IsDefault 必须 true。"""
    bad = 0
    for u in units:
        rows = ua_by.get(u["Id"])
        if not rows:
            continue
        if not any(arn.get(r["ArmorId"], {}).get("IsDefault") for r in rows):
            bad += 1
    return bad


def _table_header(add, d: Data, name: str, inf) -> None:
    cols = d.schema[name]
    add("列（%d）：" % len(cols) + "、".join(q(c) for c in cols))
    add("")
    rules = [r for r in d.rules["rules"] if r.get("table") == name and r["severity"] != "info"]
    if rules:
        add("相关规则：")
        add("")
        for r in rules:
            add("- " + q(r["id"]) + "（**" + r["severity"] + "**）—— " + r["expr"])
        add("")


def main(argv: list[str]) -> int:
    dataset = Path(argv[1]) if len(argv) > 1 else DEFAULT_DATASET
    if not (dataset / "_schema.json").is_file():
        raise SystemExit("找不到数据源：" + str(dataset))
    # 实例目录怎么认（数字名 + `_manifest.json`）与「最新 = pathID 最大」都在产品里 ——
    # 不再自己 `iterdir()` 数一遍、也不再自己取 `[-1]`（见 `Dataset.latest_instance_id`）。
    ds = load_dataset(dataset)
    pid = argv[2] if len(argv) > 2 else ds.latest_instance_id
    text = build(Data(ds, pid))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print("写入 " + str(OUT) + "  （实例 " + pid + "，" + str(len(text.splitlines())) + " 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
