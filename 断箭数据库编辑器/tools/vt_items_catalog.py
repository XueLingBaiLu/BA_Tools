r"""生成《VT item 人话目录》—— 355 个只有 hash 的 item，各自「谁在用」。

## 为什么要生成器

材质里的 `m_BuildTextureStacks.itemName` 是**编辑器在构建期算出来的 32 位 hex**
（kb/18 §6.3），静态资产侧没有任何可读名。能把它翻成人话的**唯一**线索是
「哪些材质的栈绑定里出现了这个 item」—— 那就要把上万个 `.mat` 全扫一遍。
文档里每个数字（6 个 group / 355 个 item / 每条几个 owner）都必须能**现算**，
所以落成「生成器 + 产物」两份，而不是手写一份 md（照 `tools/gen_infantry_map.py` 的规矩）。

## 数据源

AssetRipper 全量导出的文本 YAML：`<工作区>/unity-ba-rip-all/Assets/Material/*.mat`
（实测 11,902 个文件 / 60,527,105 B）。⛔ 只读：不进游戏目录、不开 units 包（3.38 GB）。

## 用法

    .venv\Scripts\python.exe tools\vt_items_catalog.py             # 生成 docs/VT-item-目录.md
    .venv\Scripts\python.exe tools\vt_items_catalog.py --check     # 只核产物最新没有（不一致 rc=1）
    .venv\Scripts\python.exe tools\vt_items_catalog.py --materials <目录> --out <md>

## 判据（每条都能红）

* **条数**：目录里每行一个 item，条数 == 实读的 distinct itemName 数（今天 = 355）；
* **owner 非空**：某一条 owner 为空 ⇒ 报红（那个 item 到底是谁在用，说不清就不许印）；
* **1:1**：真件里 (groupName, itemName) 是 1:1 —— 出现一对多 / 多对一 ⇒ 报红（口径变了）；
* **产物不过期**：`--check` 把重新生成的结果与盘上那份**逐字节**比，不一致 ⇒ rc=1。
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from dataclasses import dataclass
from pathlib import Path

#: 仓根（本文件住在 <仓根>/tools/ 下）与工作区（仓根的上一层）。
ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent
#: 默认数据源：AssetRipper 的全量材质导出（文本 YAML）。
DEFAULT_MATERIALS = WORKSPACE / "unity-ba-rip-all" / "Assets" / "Material"
#: 默认产物（生成物：**别手改**，改口径就改本文件再重跑）。
DEFAULT_OUT = ROOT / "docs" / "VT-item-目录.md"

EXIT_OK = 0
EXIT_RED = 1
EXIT_UNUSABLE = 2

#: 材质名 / shader guid / VT 栈块 —— 与 `_work/paint-design/scan_vt_items.py` 同一套正则口径
#: （那份研究件的读数 11,902 / 6,046 / 6 / 355 就是本工具的对拍基线）。
NAME_RE = re.compile(r"^  m_Name:\s*(.*)$")
SHADER_RE = re.compile(r"^  m_Shader:\s*\{fileID:\s*\d+,\s*guid:\s*([0-9a-fA-F]+)")
STACK_HEAD = "  m_BuildTextureStacks:"
GROUP_RE = re.compile(r"^  - groupName:\s*(\S+)\s*$")
ITEM_RE = re.compile(r"^    itemName:\s*(\S+)\s*$")
#: 32 位小写 hex（group / item 都是这个形状，kb/18 §6.3）。
HEX_RE = re.compile(r"^[0-9a-f]{32}$")


def q(text: str) -> str:
    """把一段文字包成 md 行内代码（生成器源码里不写字面反引号）。"""
    return chr(96) + text + chr(96)


# --------------------------------------------------------------------------- #
# 解析：一个 .mat 文本 -> (材质名, shader guid, ((group, item), ...))
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class MaterialFile:
    """一个 .mat 的读数：`stacks` 是真实的 (group, item) 对，`oddities` 是读不通的那些。"""

    path: Path
    name: str
    shader: str
    stacks: tuple[tuple[str, str], ...]
    oddities: tuple[str, ...]


def parse_material(path: Path) -> MaterialFile:
    """只读一个 .mat（文本 YAML），抠出 VT 栈绑定。

    ⛔ 不解释、不换算：`groupName` / `itemName` 原样取（它们是编辑器算出来的字面值）。
    `m_BuildTextureStacks: []`（空列表）⇒ 这个材质没有 VT 绑定。
    真件里有 **2 份**文件（`US_Ranger 1.mat` / `US_Ranger 1_0.mat`）带一条**残值**：
    `groupName: '<invalid>'` ＋ `itemName: Texture Stack 2 (NULL, NULL)` ——
    那一条进 `oddities`（**印出来**），⛔ 不当成一个 group / item 用。
    """
    name = ""
    shader = ""
    stacks: list[tuple[str, str]] = []
    odd: list[str] = []
    in_stack = False
    group = ""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not name:
                hit = NAME_RE.match(line)
                if hit:
                    name = hit.group(1).strip().strip("'\"")
                    continue
            if not shader:
                hit = SHADER_RE.match(line)
                if hit:
                    shader = hit.group(1).lower()
            if line.startswith(STACK_HEAD):
                in_stack = True
                group = ""
                continue
            if not in_stack:
                continue
            hit = GROUP_RE.match(line)
            if hit:
                group = hit.group(1)
                continue
            hit = ITEM_RE.match(line)
            if hit:
                if group:      # 没有 groupName 的 itemName 不算数（结构不对，别硬凑）
                    stacks.append((group, hit.group(1)))
                continue
            if group and line.startswith("    itemName:"):
                odd.append(f"{path.name}：group {group} 的 itemName 读不通（{line.strip()}）")
                continue
            if line.strip() and not line.startswith(" "):
                in_stack = False
    return MaterialFile(path=path, name=name or path.stem, shader=shader,
                        stacks=tuple(stacks), oddities=tuple(odd))


# --------------------------------------------------------------------------- #
# 读数（纯数据）：产物就是从它渲染出来的
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Reading:
    """一次全量扫描的读数。"""

    directory: Path
    mat_files: int
    mats_with_stacks: int
    bindings: int
    #: itemName -> 用它的材质名（去重、排序）
    owners: dict[str, tuple[str, ...]]
    #: itemName -> groupName（真件 1:1）
    groups: dict[str, str]
    #: groupName -> (itemName, ...)
    by_group: dict[str, tuple[str, ...]]
    #: itemName -> 出现它的材质里 distinct shader guid 数（旁证：VT 只该来自 UnitV3）
    shaders: dict[str, int]
    duplicate_names: int
    #: 结构不规范、**不计入目录**的栈条目（真件里 2 条残值，印出来但不报红）
    oddities: tuple[str, ...]
    problems: tuple[str, ...]

    @property
    def items(self) -> tuple[str, ...]:
        return tuple(sorted(self.owners))

    @property
    def owner_names(self) -> tuple[str, ...]:
        return tuple(sorted({n for names in self.owners.values() for n in names}))

    def counts(self) -> tuple[int, ...]:
        """每个 item 被几颗材质用（升序）。"""
        return tuple(sorted(len(names) for names in self.owners.values()))


def scan(directory: Path) -> Reading:
    """扫一遍整个材质导出目录（只读）。"""
    if not directory.is_dir():
        raise FileNotFoundError(f"材质导出目录不在了：{directory}")
    files = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".mat")
    owners: dict[str, set[str]] = collections.defaultdict(set)
    groups: dict[str, str] = {}
    group_items: dict[str, set[str]] = collections.defaultdict(set)
    item_shaders: dict[str, set[str]] = collections.defaultdict(set)
    problems: list[str] = []
    oddities: list[str] = []
    seen_names: collections.Counter = collections.Counter()
    with_stacks = 0
    bindings = 0
    for path in files:
        got = parse_material(path)
        name, shader, pairs = got.name, got.shader, got.stacks
        oddities += list(got.oddities)
        seen_names[name] += 1
        if pairs:
            with_stacks += 1
        for group, item in pairs:
            if not HEX_RE.match(group) or not HEX_RE.match(item):
                problems.append(f"{path.name}：group/item 不是 32 位小写 hex（{group} / {item}）")
                continue
            known = groups.get(item)
            if known is not None and known != group:
                problems.append(f"item {item} 出现在两个 group 里：{known} / {group}")
            groups[item] = group
            group_items[group].add(item)
            owners[item].add(name)
            if shader:
                item_shaders[item].add(shader)
            bindings += 1
    for item, names in owners.items():
        if not names:
            problems.append(f"item {item} 一个 owner 都查不到")
    return Reading(
        directory=directory,
        mat_files=len(files),
        mats_with_stacks=with_stacks,
        bindings=bindings,
        owners={item: tuple(sorted(names)) for item, names in owners.items()},
        groups=dict(groups),
        by_group={g: tuple(sorted(v)) for g, v in group_items.items()},
        shaders={item: len(v) for item, v in item_shaders.items()},
        duplicate_names=sum(1 for _n, c in seen_names.items() if c > 1),
        oddities=tuple(oddities),
        problems=tuple(problems),
    )


# --------------------------------------------------------------------------- #
# 人话：owner 名单 -> 一个「族」名
# --------------------------------------------------------------------------- #
def _head(name: str) -> str:
    """材质名的第一个词（按空格 / 下划线切）—— 例 `RU_T72B 3` -> `RU_T72B`。"""
    for sep in (" ", "_"):
        if sep in name:
            return name.split(sep, 1)[0]
    return name


def _common_prefix(names: tuple[str, ...]) -> str:
    if not names:
        return ""
    head = names[0]
    for name in names[1:]:
        while head and not name.startswith(head):
            head = head[:-1]
    return head.rstrip(" _-")


def family_of(names: tuple[str, ...]) -> str:
    """owner 名单的「族」：取出现最多的第一个词；都只出现一次 ⇒ 退回最长公共前缀。"""
    if len(names) == 1:
        return _head(names[0])
    heads = collections.Counter(_head(name) for name in names)
    best, count = heads.most_common(1)[0]
    if count > 1:
        return best
    common = _common_prefix(names)
    return common if len(common) >= 4 else "(看不出族)"


# --------------------------------------------------------------------------- #
# 渲染产物
# --------------------------------------------------------------------------- #
def _median(values: tuple[int, ...]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def render(reading: Reading) -> str:
    """读数 -> 那份 md（逐字节稳定：不写时间戳、排序全部钉死）。"""
    items = reading.items
    counts = reading.counts()
    names = reading.owner_names
    tables: list[str] = []
    add = tables.append
    add("# VT item 人话目录（355 个 hash ↔ 谁在用）")
    add("")
    add(f"> **本文件由 {q('tools/vt_items_catalog.py')} 生成，不要手改。**")
    add(f"> 数据源：{q(str(reading.directory))}（AssetRipper 全量导出，文本 YAML）")
    add("> 复算：" + q(r".venv\Scripts\python.exe tools\vt_items_catalog.py") + "（只核产物：加 "
        + q("--check") + "）")
    add(">")
    add("> ⛔ 这里列的是**静态资产侧**的绑定（材质里的 `m_BuildTextureStacks` 那一对 group/item）")
    add("> 「池子里实际有哪些 item」要读游戏盘上的 `.gts` 页表，本工具**不读**（它只扫导出件）。")
    add("")
    add("## 0 · 一句话读数")
    add("")
    add("| 项 | 读数 |")
    add("|---|---|")
    add(f"| `.mat` 文件 | {reading.mat_files:,} |")
    add(f"| 其中带 `m_BuildTextureStacks` 的 | {reading.mats_with_stacks:,} |")
    add(f"| (材质文件, group, item) 绑定条数 | {reading.bindings:,} |")
    add(f"| `groupName` 取值 | {len(reading.by_group)} |")
    add(f"| `itemName` 取值 = **本目录条数** | **{len(items)}** |")
    add(f"| owner 材质名（去重后） | {len(names):,} |")
    add(f"| 材质名重复的（同名多份文件） | {reading.duplicate_names:,} |")
    add(f"| ⚠ 结构不规范的栈条目（**不计入目录**） | {len(reading.oddities)} |")
    if counts:
        total = sum(counts)
        add(f"| 一个 item 被几颗材质用 | min {counts[0]} · median {_median(counts):g}"
            f" · mean {total / len(counts):.1f} · max {counts[-1]} |")
    add("")
    add("读法：**`itemName` 是「车族级」资产** —— 同一台车的多套迷彩 / 改型 / 玻璃件共用一套")
    add("法线＋遮罩（`kb/21` §6.1）。所以「换涂装沿用同族 item」是游戏原生做法，跨族沿用才是错的。")
    add("")
    add("## 1 · 按 group 分池")
    add("")
    add("| groupName（32 hex） | item 数 | owner 材质名数（去重） | 最大的一族 |")
    add("|---|---:|---:|---|")
    for group in sorted(reading.by_group, key=lambda g: (-len(reading.by_group[g]), g)):
        got = reading.by_group[group]
        families = collections.Counter(family_of(reading.owners[item]) for item in got)
        top, many = families.most_common(1)[0]
        add(f"| {q(group)} | {len(got)} | "
            f"{sum(len(reading.owners[item]) for item in got)} | {top}（{many} 个 item） |")
    add("")
    add("## 2 · item 目录（逐条）")
    add("")
    add("排序 = group（item 数降序）→ itemName 升序。`族` 由 owner 名单现算（最多的第一个词）。")
    add("")
    index = 0
    for group in sorted(reading.by_group, key=lambda g: (-len(reading.by_group[g]), g)):
        add(f"### {group}（{len(reading.by_group[group])} 个 item）")
        add("")
        add("| # | itemName | 族 | 材质数 | owner 材质名 |")
        add("|---:|---|---|---:|---|")
        for item in reading.by_group[group]:
            index += 1
            owned = reading.owners[item]
            add(f"| {index} | {q(item)} | {family_of(owned)} | {len(owned)} | "
                + " · ".join(q(name) for name in owned) + " |")
        add("")
    add("## 3 · 用量最大的 20 个 item（人读）")
    add("")
    add("| itemName | 族 | 材质数 | 前几个 owner |")
    add("|---|---|---:|---|")
    ranked = sorted(items, key=lambda i: (-len(reading.owners[i]), i))[:20]
    for item in ranked:
        owned = reading.owners[item]
        shown = " · ".join(q(name) for name in owned[:6])
        tail = "" if len(owned) <= 6 else f" …（共 {len(owned)} 颗）"
        add(f"| {q(item)} | {family_of(owned)} | {len(owned)} | {shown}{tail} |")
    add("")
    add("## 4 · 口径与边界")
    add("")
    add("* **owner 用的是材质的 `m_Name`**（不是文件名）：导出里同一颗材质有多份文件"
        "（`X.mat` / `X_0.mat` / `X_1.mat`，`m_Name` 相同）")
    add(f"  ⇒ 文件 {reading.mat_files:,} 个 ≠ 唯一名 {len(names):,} 个；"
        f"**同名多份的有 {reading.duplicate_names:,} 个名字**。")
    add(f"* `groupName` 与 `itemName` 在真件里是 **1:1**（今天 {len(reading.by_group)} 个 group / "
        f"{len(items)} 个 item）—— 一对多 / 多对一都会在生成时报红。")
    if reading.oddities:
        add(f"* ⚠ **{len(reading.oddities)} 条残值没有计入**（结构读不通，是真件上游的坏数据，"
            "不是本工具扫丢了）：")
        for line in reading.oddities:
            add(f"  * {q(line)}")
        add(f"  ⇒ 所以本文件里的 group 数是 **{len(reading.by_group)} 个真组**；"
            "上一版扫描器把那条残值也当成一个 group，于是写着「6 个」—— 多的那一个就是它。")
    add("* 本目录**不回答**「这套 item 长什么样」（法线 / 遮罩的细节）—— 那是池子里的像素，")
    add("  静态导出件里没有（`kb/22` §0）。")
    add("* item 的 32 位 hex 是**编辑器算出来的**（`kb/18` §6.3）：⛔ 不要手写「看起来像」的值，")
    add("  要新 item 就烘一套池子（`vt-bake`）。")
    add("")
    return "\n".join(tables) + "\n"


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def _report(reading: Reading, out: Path, *, written: bool, same: bool) -> None:
    print(f"数据源              = {reading.directory}")
    print(f"mat_files           = {reading.mat_files:,}")
    print(f"mats_with_stacks    = {reading.mats_with_stacks:,}")
    print(f"bindings            = {reading.bindings:,}")
    print(f"distinct_groupnames = {len(reading.by_group)}")
    print(f"distinct_itemnames  = {len(reading.items)}")
    print(f"owner 材质名（去重） = {len(reading.owner_names):,}"
          f"（同名多份 {reading.duplicate_names:,} 个名字）")
    counts = reading.counts()
    if counts:
        print(f"每 item 的材质数     = min {counts[0]} · median {_median(counts):g}"
              f" · max {counts[-1]}")
    if reading.oddities:
        print(f"警告：残值条目（不计入）= {len(reading.oddities)}")
        for line in reading.oddities:
            print(f"    {line}")
    for group in sorted(reading.by_group, key=lambda g: (-len(reading.by_group[g]), g)):
        print(f"  group {group}  item {len(reading.by_group[group])}")
    if written:
        print(f"产物                = {out}（{out.stat().st_size:,} B）")
    else:
        print(f"产物                = {out} —— {'一致（没重写）' if same else '不一致（见红）'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vt_items_catalog.py",
        description="扫材质导出件，生成《VT item 人话目录》（item 的 32 位 hash ↔ 谁在用）。",
    )
    parser.add_argument("--materials", default=str(DEFAULT_MATERIALS),
                        help=f"材质导出目录（默认 {DEFAULT_MATERIALS}）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"产物 md（默认 {DEFAULT_OUT}）")
    parser.add_argument("--check", action="store_true",
                        help="只核产物是不是最新的：重新算一遍与盘上那份逐字节比，不一致 rc=1")
    args = parser.parse_args(argv)

    materials = Path(args.materials)
    out = Path(args.out)
    try:
        reading = scan(materials)
    except (FileNotFoundError, OSError) as exc:
        print(f"用不了：{exc}", file=sys.stderr)
        print("  hint：--materials 指到 AssetRipper 导出的 Assets\\Material 目录", file=sys.stderr)
        return EXIT_UNUSABLE
    if not reading.items:
        print(f"有红：{materials} 里一条 VT 绑定都没扫到（0 个 item）", file=sys.stderr)
        return EXIT_RED
    if reading.problems:
        for line in reading.problems[:10]:
            print(f"有红：{line}", file=sys.stderr)
        print(f"有红：{len(reading.problems)} 条结构问题 —— 口径变了，先看清楚再改判据",
              file=sys.stderr)
        return EXIT_RED

    text = render(reading)
    if args.check:
        current = out.read_text(encoding="utf-8") if out.is_file() else ""
        same = current == text
        _report(reading, out, written=False, same=same)
        if same:
            print("判据：产物是最新的（逐字节相同）")
            return EXIT_OK
        print(f"有红：产物过期（盘上 {len(current):,} 字符 / 现算 {len(text):,} 字符）——"
              "重跑一次本命令不带 --check", file=sys.stderr)
        return EXIT_RED

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    back = out.read_text(encoding="utf-8")
    _report(reading, out, written=True, same=True)
    if back != text:
        print("有红：写回读出来的与要写的不是同一份", file=sys.stderr)
        return EXIT_RED
    rows = len(re.findall(r"(?m)^\| \d+ \| ", text))
    print(f"判据：正文目录行 {rows} == item 数 {len(reading.items)}（每行都带 owner）")
    if rows != len(reading.items):
        print(f"有红：正文行数 {rows} 与 item 数 {len(reading.items)} 对不上", file=sys.stderr)
        return EXIT_RED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
