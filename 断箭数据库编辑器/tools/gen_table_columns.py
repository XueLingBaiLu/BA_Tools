"""生成 docs/数据-表与列清单.md —— 24 张表的列级清单（生成物，别手改）。

为什么要有它：卡片上很多"没研究出来"的东西，其实**数据库里就有**。
反编译只回答"读哪一列、什么格式、什么条件"，列本身要在这张清单里查。
换游戏版本后重跑一次即可。

用法：.venv/Scripts/python.exe tools/gen_table_columns.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ba_mod_maker.core.data import find_dataset_root, load_dataset  # noqa: E402

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

BT = chr(96)  # 反引号：Markdown 里包列名用

#: 列名 -> 这一列干什么用、喂信息卡哪一格。**手写的部分只有这里**，
#: 其余（类型/样例/取值分布）都是扫出来的。
NOTES: dict[tuple[str, str], str] = {
    ("Units", "Cost"): "单位基础价（信息卡左上大数字 = 它 + 所选 Options 的 Cost 增量）",
    ("Units", "OriginalCost"): "⚠️ 全库都是 0 —— 别拿它当基础价",
    ("Units", "Type"): "2 步兵 / 4 载具 / 8 直升机 / 16 固定翼 / 32 舰船",
    ("Units", "CategoryType"): "军械库左栏分类",
    ("Units", "Stealth"): "隐身（信息卡 隐身 那一格）",
    ("Units", "CurrentStealth"): "同上，运行时那份；两者取值分布一致",
    ("Units", "Weight"): "步兵恒为 1；载具是整车重量（步兵重量 = 125 × 人数，要自己算）",
    ("Units", "TimeModifierRefund"): "撤回时间修正（距离撤回至战斗群 那一格）",
    ("Units", "InfantrySlots"): "座位数（载具那一格）",
    ("Units", "DisplayInArmory"): "军械库列表要不要显示它",
    ("Weapons", "Type"): "1 步枪 / 60 榴弹 / 110 主炮 / 116 航炮 / 140 火箭巢 …",
    ("Weapons", "MagazineSize"): "弹匣容量",
    ("Weapons", "AimTimeMin"): "瞄准时间（min==max 只打一个数）",
    ("Weapons", "AimTimeMax"): "瞄准时间上限",
    ("Weapons", "ShotsPerBurstMin"): "点射发数（RateOfFireSecond 的输入）",
    ("Weapons", "ShotsPerBurstMax"): "点射发数上限",
    ("Weapons", "TimeBetweenBurstsMin"): "点射间隔（RateOfFireSecond 的输入）",
    ("Weapons", "TimeBetweenBurstsMax"): "点射间隔上限",
    ("Weapons", "TimeBetweenShotsInBurst"): "点射内每发间隔",
    ("Weapons", "MagazineReloadTimeMin"): "装填时间（min==max 只打一个数）",
    ("Weapons", "MagazineReloadTimeMax"): "装填时间上限",
    ("Weapons", "ShortGroundAttackBurst"): "AI 短点射发数（与 ShotsPerBurst 不是一回事）",
    ("Weapons", "NormalGroundAttackBurst"): "AI 常规点射发数",
    ("Weapons", "LongGroundAttackBurst"): "AI 长点射发数",
    ("Weapons", "MultiTargetTracking"): "多目标跟踪（信息卡多目标那一行）",
    ("Weapons", "SimultaneousTracking"): "同时跟踪数",
    ("Ammunitions", "HUDName"): "弹药名（弹种那一行）",
    ("Ammunitions", "HUDMultiplier"): "显示倍数：弹药数量与射速都要乘它（AK 是 3）",
    ("Ammunitions", "SupplyCost"): "补给值（kg）；补给消耗还要乘 UnitInfoCard 的静态修正",
    ("Ammunitions", "ResupplyTime"): "补给时间（>1 才发射）",
    ("Ammunitions", "Damage"): "伤害 = RoundToNearestMultiple(Damage, 0.05)",
    ("Ammunitions", "StressDamage"): "压制伤害（还没进卡片）",
    ("Ammunitions", "IgnoreCover"): "无视掩体 ← 未解那一行就是它",
    ("Ammunitions", "CanBeIntercepted"): "可被主动防御系统拦截 ← 未解那一行",
    ("Ammunitions", "DispersionHorizontalRadius"): "武器散布（水平）← 未解那一行的候选",
    ("Ammunitions", "DispersionVerticalRadius"): "武器散布（垂直）",
    ("Ammunitions", "DispersionMinimal"): "散布下限",
    ("Ammunitions", "ArmorTargeted"): "0 无 / 1 动能 / 2 爆炸 —— 穿深那一格的类型后缀",
    ("Ammunitions", "PenetrationAtGroundRange"): "地面射程上的穿深",
    ("Ammunitions", "PenetrationAtMinRange"): "最小射程上的穿深",
    ("Ammunitions", "HealthAOERadius"): "爆炸半径（显示时 ×2）",
    ("Ammunitions", "TrajectoryType"): "10 直射 / 20 抛射 / 100 导弹 / 400 炸弹…",
    ("Ammunitions", "GroundRange"): "地面射程（显示时 ×2）",
    ("Ammunitions", "LowAltRange"): "低空射程（显示时 ×2）",
    ("Ammunitions", "HighAltRange"): "高空射程（显示时 ×2）",
    ("Ammunitions", "MinimalRange"): "最小射程（>0 时射程打区间）",
    ("Ammunitions", "MaxSpeed"): "弹药最大速度（信息卡 最大速度 那一行）",
    ("Ammunitions", "AimTimeMinOverride"): "瞄准时间覆盖（非 0 时盖掉武器的 AimTime）",
    ("Ammunitions", "AimTimeMaxOverride"): "瞄准时间覆盖上限",
    ("Ammunitions", "Seeker"): "导引头类型（导引头那一行）",
    ("Ammunitions", "TargetType"): (
        "可打目标的位掩码（dump.cs TargetType）：1 地面 / 2 步兵 / 4 载具 / 8 直升机 / 16 固定翼 / "
        "32 舰船 / 128 投射物 / 256 防空压制 / 512 巡航导弹 / 1024 弹道导弹。"
        "❗ 旧说明写成「1 步兵」是错的；卡片上只画 2/4/8/16 与导弹那四位（见研究 §9.9）"
    ),
    ("Armors", "FrontArmor"): "正面装甲（装甲四格）",
    ("Mobility", "MaxSpeedRoad"): "前进速度（步兵/载具/直升机都读它）",
    ("Mobility", "MaxSpeedReverse"): "倒车速度（只对 Type==4 发射）",
    ("Mobility", "MaxSpeedWater"): "水面速度（舰船那一格，**强假设**）",
    ("Mobility", "TurnRate"): "转向速度",
    ("Mobility", "IsAmphibious"): "两栖",
}


def main(*, corpus_path: Path | None = None, target_path: Path | None = None) -> int:
    dataset = load_dataset(find_dataset_root())
    readpoints, value_sem = load_corpora(corpus_path)
    out: list[str] = ["# 数据表与列清单（生成物，别手改）"]  # 与上行合一：行号被别处引用（见文末）
    out.append("")
    out.append("由 tools/gen_table_columns.py 生成；手写部分（说明列）在脚本的 NOTES 里。")
    out.append("")
    out.append("**为什么有这份东西**：卡片上很多「没研究出来」的格子，其实**数据库里就有** ——")
    out.append("反编译只回答「读哪一列、什么格式、什么条件」，列本身在这里查。")
    out.append("「非默认行数」= 与出现最多的那个取值不同的行数：0 就是全表同值（多半是占位）。")
    out.append(READPOINT_LEGEND + VALUE_LEGEND)
    out.append("")
    for path_id in sorted(dataset.instances):
        instance = dataset.instance(path_id)
        out.append(f"## 实例 {path_id}\n")
        for table_name in sorted(instance.table_names):
            table = instance.table(table_name)
            out.append(f"### {table_name} —— {len(table.rows)} 行 × {len(table.columns)} 列")
            out.append("")
            out.append("| # | 列 | 取值种类 | 非默认行数 | 样例 | 读点 | 说明 |")
            out.append("|---|---|---|---|---|---|---|")
            for index, column in enumerate(table.columns):
                values = [row[index] for row in table.rows]
                counter = Counter(values)
                _, top_count = counter.most_common(1)[0]
                sample = " / ".join(str(value) for value, _ in counter.most_common(3))
                if len(sample) > 44:
                    sample = sample[:44] + "…"
                note = NOTES.get((table_name, column), "")
                readpoint = readpoint_cell(readpoints, table_name, column)
                generated = value_cell(value_sem, table_name, column)
                out.append(
                    f"| {index} | {BT}{column}{BT} | {len(counter)} | {len(values) - top_count} "
                    f"| {sample or '—'} | {readpoint} | {merge_note(note, generated)} |"
                )
            out.append("")
    target = target_path or TARGET
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"写出 {target}（{len(out)} 行）")
    return 0


# --------------------------------------------------------------------------- #
# 「代码判值」—— 表.列 → 代码里**判过**哪些取值（第二份语料，同样只在生成期读一次）
#
# 为什么值得占说明列那点位置：实测 407 个 (表,列) 里只有 8 列的模型字段被常量判断过
# （int-enum 5 / bool 3；float 177 列与 string 49 列没有"判值"概念），**少但硬**：
#   Ammunitions.TrajectoryType → 100/110/120/200/300/666←IsMissile；400/410←IsBomb
# 每条都带 文件:行 出处（tools/value_domains.py 抽的），边界与假阳性写在 kb/15 §4.9。
# --------------------------------------------------------------------------- #
VALUES = WORK / "value_domains" / "value_domains.tsv.gz"
VALUE_MARK = "[代码判值]"

#: 图例（与 READPOINT_LEGEND **同一行拼接**，避免在 main 之前插行 —— 112-116 被别处按行号引用）。
VALUE_LEGEND = (
    "「[代码判值]」= 这一列在代码里被**常量判断过**的取值（写法 值←方法，多段用 ；）："
    "例 Ammunitions.TrajectoryType 的 100/110/120/200/300/666←IsMissile。来自 ISIL 的 cmp 指令"
    "（tools/value_domains.py，边界与假阳性见 kb/15 §4.9）：只对 int-enum / bool 有意义"
    "（float 是范围、string 没有判值）—— 实测 407 列里只有 8 列抽得到。"
    "⛔ 它**不是**「有效取值白名单」：switch 跳转表与跨类型读者都会漏，缺一个值不等于游戏不认。"
)


def merge_note(note: str, generated: str) -> str:
    """手写说明与生成语义合一格：**手写在前**，生成在后并带标记（纯函数）。"""
    if not generated:
        return note
    return f"{note} ｜ {generated}" if note else generated


def value_cell(mapping: dict[tuple[str, str], str] | None, table: str, column: str) -> str:
    """代码判值那一格：**语料不在 → 空**（⛔ 不许写成"没有判值"，那是撒谎）；这一列没抽到 → 空。"""
    if mapping is None:
        return ""
    got = mapping.get((table, column))
    return f"{VALUE_MARK} {got}" if got else ""


def render_value_semantics(pairs: list[tuple[int, set[str]]]) -> str:
    """[(值, {方法})] → 100/110/200←IsMissile；400/410←IsBomb（纯函数）。

    同方法组合并到一段（一个值被判多次也只出现一次），段内按值排序，段间按最小值排序。
    """
    groups: dict[frozenset[str], list[int]] = {}
    for value, methods in pairs:
        groups.setdefault(frozenset(methods), []).append(value)
    parts = []
    for methods, values in sorted(groups.items(), key=lambda kv: min(kv[1])):
        parts.append("/".join(str(v) for v in sorted(values)) + "←" + "/".join(sorted(methods)))
    return "；".join(parts)


def load_corpora(corpus_path: Path | None = None) -> tuple[dict | None, dict | None]:
    """一次读两份语料：(读点, 代码判值)；任一份读不到就该项 None（生成照跑）。"""
    return load_readpoints(corpus_path), load_value_semantics(corpus_path)


def load_value_semantics(
    corpus_path: Path | None = None, values_path: Path | None = None
) -> dict[tuple[str, str], str] | None:
    """合并两份语料：db_columns（拿 表→模型类型）+ value_domains（拿 类型.字段→取值）。

    产出 {(规范表名, 列): 语义串}，例 {("Ammunitions", "TrajectoryType"): "400/410←IsBomb"}。

    读不到就返回 None 并打降级提示 —— 与「读点」列同一口径：⛔ 产品运行时不依赖 _work。
    """
    import gzip

    from ba_mod_maker.core.data import loader

    corpus = corpus_path or CORPUS
    values = values_path or VALUES
    try:
        with gzip.open(corpus, "rt", encoding="utf-8") as handle:
            head = handle.readline().rstrip("\n").split("\t")
            rows = [line.rstrip("\n").split("\t") for line in handle if line.strip()]
        with gzip.open(values, "rt", encoding="utf-8") as handle:
            vhead = handle.readline().rstrip("\n").split("\t")
            vrows = [line.rstrip("\n").split("\t") for line in handle if line.strip()]
    except (OSError, UnicodeDecodeError) as exc:
        print(f"⚠️ 降级：代码判值语料读不到（{exc}）⇒「说明」列不加代码判值，清单照常生成。")
        return None
    try:
        t_at, c_at, m_at = head.index("table"), head.index("column"), head.index("model_type")
        vt_at, vf_at = vhead.index("type"), vhead.index("field")
        vv_at, vm_at = vhead.index("value"), vhead.index("method")
    except ValueError:
        print("⚠️ 降级：代码判值语料表头不认识 ⇒「说明」列不加代码判值，清单照常生成。")
        return None

    def norm(text: str) -> str:
        return "".join(ch for ch in text.lower() if ch.isalnum())

    type_of = {(r[t_at], r[c_at]): norm(r[m_at]) for r in rows if len(r) > m_at}
    per: dict[tuple[str, str], dict[int, set[str]]] = {}
    for r in vrows:
        if len(r) <= vm_at:
            continue
        key = (norm(r[vt_at]), r[vf_at])
        method = r[vm_at].split("(", 1)[0].rsplit(" ", 1)[-1]
        per.setdefault(key, {}).setdefault(int(r[vv_at]), set()).add(method)
    out: dict[tuple[str, str], str] = {}
    for (entry_table, column), mtype in type_of.items():
        got = per.get((mtype, column))
        if got:
            out[(loader.canonical_table_name(entry_table), column)] = render_value_semantics(
                list(got.items())
            )
    return out


# --------------------------------------------------------------------------- #
# 「读点」列 —— 表.列 → 谁读它（数据源在 _work，**只在生成期读一次**）
#
# ⚠️ 这一节（含 CORPUS / TARGET / READPOINT_LEGEND）特意写在 main() **之后**：
#    tests/test_dictionary_window.py:170 与 src/ba_mod_maker/app/dialogs/dictionary_data.py:30
#    都按行号引用 main 里的统计口径（两处都写着「tools/gen_table_columns.py:112-116」）——
#    把那一块原地钉住，比去改两个别的文件省事；同理，import 也留在函数里
#    （tools/** 的 per-file-ignores 本来就开着 PLC0415）。
# --------------------------------------------------------------------------- #
CORPUS = WORK / "field_index" / "db_columns.tsv.gz"
TARGET = ROOT / "docs" / "数据-表与列清单.md"
PLACEHOLDER = "—"

#: 文首图例：读点这一列能说明什么、**不能**说明什么（边界见 kb/15-反编译索引.md §4.5）。
READPOINT_LEGEND = (
    "「读点」= 这一列被哪些方法读过（`×N` = 读 N 次，N=1 省略；最多列 8 个，"
    "超出写 `…+N`；列不全时以 `_work/field_index/db_columns.tsv.gz` 的 `n_readers` 列为准）。\n"
    "读点来自 ISIL 归属规则（只认 this=rcx 及其别名）⇒ 跨类型/被内联的读点会漏；"
    "没有读点 ≠ 死列（反例：Units.ModelFileName 只归属到它自己的 getter，而它确实是模型加载键）。"
    "同理，「只看到 GetRestData 读」也只是跨类型的读点没归到它名下，别当成「只进 API」。"
    "数据源 `_work/field_index/db_columns.tsv.gz`（`tools/field_index.py --db-columns` 生成）："
    "只在生成期读一次，语料不在时这一列留空 —— 产品运行时不依赖 `_work`。"
)


def render_readers(top_readers: str) -> str:
    """语料那一格 → 清单里那一格（纯函数，不碰盘）。

    `IsMissile()x6 IsBomb()x2 get_TrajectoryType()x1`
    → `IsMissile×6 · IsBomb×2 · get_TrajectoryType`
    （`×N` 只标 N>1；名字末尾那对括号去掉；语料里偶见缺左括号的 `armorType)x1`，照样认。）
    """
    parts: list[str] = []
    for token in top_readers.split():
        name, count = _split_reader(token)
        if name:
            parts.append(name if count in ("", "1") else f"{name}×{count}")
    return " · ".join(parts)


def _split_reader(token: str) -> tuple[str, str]:
    """`IsMissile()x6` → (`IsMissile`, `6`)；没有 `xN` 尾巴就把整串当名字（`foo_x2` 不误切）。"""
    name, sep, count = token.rpartition("x")
    if sep and count.isdigit() and name.endswith(("(", ")")):
        return name.rstrip("()"), count
    return token, ""


def readpoint_cell(readpoints: dict[tuple[str, str], str] | None, table: str, column: str) -> str:
    """读点那一格：语料可用、但这一列没有归属读点 → `—`；**整个语料不在** → 留空。

    ⛔ 两种降级必须分开：语料缺失时写成「每一列都没有读点」就是撒谎（负控量的正是这个）。
    """
    if readpoints is None:
        return ""
    return readpoints.get((table, column)) or PLACEHOLDER


def load_readpoints(path: Path | None = None) -> dict[tuple[str, str], str] | None:
    """读 `_work/field_index/db_columns.tsv.gz` → `{(表, 列): 读点}`；**读不到返回 None**。

    读不到时：生成照常跑完、读点列留空，stdout 打一句降级提示。⛔ 产品运行时不依赖 `_work`，
    这一读只发生在生成期，而且这一列只是**提示**不是判据（边界见 READPOINT_LEGEND）。

    表名过一遍 `loader.canonical_table_name`：语料按**条目名**建（`PlaneFlyPresets`），
    清单里写的是**规范表名**（`FlyPresets`）——这一对映射全仓只在 loader 里登记了一次。
    """
    import gzip

    from ba_mod_maker.core.data import loader

    corpus = path or CORPUS
    try:
        with gzip.open(corpus, "rt", encoding="utf-8") as handle:
            head = handle.readline().rstrip("\n").split("\t")
            cells = [line.rstrip("\n").split("\t") for line in handle if line.strip()]
    except (OSError, UnicodeDecodeError) as exc:
        print(f"⚠️ 降级：读点语料读不到（{corpus} —— {exc}）⇒「读点」列留空，清单照常生成。")
        return None
    try:
        table_at = head.index("table")
        column_at = head.index("column")
        readers_at = head.index("top_readers")
    except ValueError:
        print(f"⚠️ 降级：读点语料表头不认识（{head}）⇒「读点」列留空，清单照常生成。")
        return None
    readpoints: dict[tuple[str, str], str] = {}
    for row in cells:
        if len(row) <= readers_at:
            continue
        table = loader.canonical_table_name(row[table_at])
        readpoints[(table, row[column_at])] = render_readers(row[readers_at])
    return readpoints


if __name__ == "__main__":
    raise SystemExit(main())
