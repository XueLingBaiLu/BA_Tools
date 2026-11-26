r"""单位 prefab 的**节点命名契约闸**（L7）—— 给一棵 prefab 节点树判红/绿（**纯函数**）。

## 这一层收的是哪件事

《断箭》的 UnitBuilder 挂上一个单位 prefab 之后**按名字**去 Find 节点（护甲 / 附加模型 / 机动 /
骨骼容器 / 登机点）。名字对不上 ⇒ **静默失效**：模型照样显示，但轮子不转、护甲不切、
登机点为空（这一族 = 交接文档说的「B 级静默失效」）。

`unit_shape.py`（A6 的读侧）已经有「引擎按什么规则比名字」的**匹配器**；本模块补的是另一半：

    unit_shape      : 名字集合 -> 每条契约**命中没命中**（给读数用）
    node_contract   : 一棵**树** -> 逐条契约**逐级点名**谁不符（给闸用，能红）

两半分工写在 kb/19「唯一判据」的口径上：**一把尺子只做一件事** —— 本模块不重新实现匹配规则，
能借 `unit_shape.wheel_side / suspension_side / track_side / recoil_index` 的一律借。

## 「能红」是怎么做到的：近亲必须落进契约

每条硬规则写成一对：

    loose   引擎用**宽松**口径能找到它（例：ToLower 之后等于 "chassis" / 前缀匹配 / 子串匹配）
    strict  真件里它**实际**长什么样（例：逐字 "Chassis"）

**命中 loose 而不过 strict ⇒ 违规**。这就是静默失效那一族的形状：
把它写成 `chassis`，引擎的 `Transform.FindChild("Chassis")`（`String.op_Equality`，大小写敏感）
**照样找不到**，而 A0~A6 全绿（那些闸只看 PPtr 在不在包里）。

⚠ 反过来，引擎**本来就大小写不敏感**的地方（`BoneContainer.Bake` 对 root/body 是 `ToLower()` 后
全等）**不许红**：原版 285 件里 `US_LAV_AT-M_body` 那颗就是 `Body` 大写 B，游戏不认它才是 bug。
用例里有一条**负变异**专门钉这个（`Body` 必须仍然绿）。

## 硬规则是怎么选的：**先在 285 件上量，零违规才准进闸**

复算：`.venv\\Scripts\\python.exe _work\\l7\\measure_contract.py`（吃冻结抽件，不开包）
全扫：`.venv\\Scripts\\python.exe _work\\l7\\scan_285.py`

| 规则 | 285 件实测 | 依据 |
|---|---|---|
| `unit_root.name` 根名 == 资产名 | 285/285 | 研究 §7②「顶层名 = 单位名 285/285 唯一」 |
| `root.present` / `body.present` | 285/285 | BoneContainer.Bake：`ToLower()=="root"/"body"` |
| `chassis.case` 逐字 `Chassis` | 287/287 | UnitBuilder.InitArmors 的 `Transform.FindChild` |
| `base_*.case` / `base_*.under_chassis` | 135/135 | InitArmors 先 FindChild(Chassis) |
| `wheel.case` / `suspension.case` 首字母大写 | 4310/4310 | Wheels/Suspension 比字面量 |
| `skinned.index_digits` 三位 | 1469/1469 | 研究 §7② 的 `<NNN>_skinned_<状态>` |
| `component.UnitPrefabRoot_on_root` | 285/285 | UnitPrefabRoot 就是根上那颗（出厂闸 A1 同源） |
| `component.AnimationManager_on_root` | 283/283 | 有则必须在根上 |

**故意不硬**（真件自己就有例外 ⇒ 进了闸就是假红）：

* `Chassis` **必须是根直系子**：实测 278 个在深度 1、**9 个在深度 2/4**（US_MH60X 等 7 件）
  ⇒ 只记读数；
* `body` **必须在 `root` 之下**：实测 284 里 277 在 `root` 下、7 在 `root_scale` 下、
  `US_LAV_AT-M_body` 的 `Body` 在 `Chassis/base` 下 ⇒ 只记读数；
* 轮/悬挂**位数必须 3**：实测 3 位 4268 个、**4 位 38 个、1 位 4 个**（RU_BUKM2 的 `R1`）
  ⇒ 只比大小写；
* `turret_N/weapon_N/shell_spawn_i_j` 的**严格父子链**：实测 `weapon_0_1` 这种带下标的武器变体
  会把「shell 必须在 weapon_ 下」的严格式判红 277 条 ⇒ 只记读数；
* `trackl/trackr` 子串族：`unit_shape` 的注释已经记着真件 `RU_TanktrakL` 不含 `trackl` ⇒ 不进闸；
* `lod_*` / `VFXPoint_*` / `(POLY FEW)_LODS-DON'T-DELETE-MANUALLY` / `recoil*`：**只是命名约定**、
  引擎不按名字找它们（`weapon_*` / `shell_spawn_*` 同理，`prefab_build.UNIT_NODE_CONTRACT`
  判 `data_only`）⇒ 只记读数。

## 边界

* **纯函数**：一个包都不开、一个字节都不写；输入是归一化节点序列（`unitypy_source.read_prefab_nodes`
  的契约）或任何有 `.nodes` 的树（`prefab.PrefabTree`）；
* 这里**不**判「PPtr 在不在包里」那些事 —— 那是出厂闸 A0~A6（`unit_bundle.verify_unit_bundle`）；
* 真件的 `root` / `body` **不一定**挂在根上（`RU_SU34` 的 `root` 就在机型名节点下）⇒ 本模块只看
  **名字**与**直接父名**，不看绝对深度（除了那两条明确写了层级的规则）。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ba_mod_maker.asset_kernel import unit_shape

__all__ = [
    "HARD_KEYS",
    "RULES",
    "ContractRule",
    "NodeContractReport",
    "Violation",
    "check_nodes",
    "check_tree",
    "frozen_entry_to_nodes",
    "normalized",
]

#: 严重度：`hard` 的规则一违规就**红**（别装机）；`reading` 只进读数。
IMPACT_SILENT = "silent_failure"
IMPACT_LOOKUP = "lookup"
IMPACT_CONVENTION = "convention"

#: 三档各自的**人话**（⛔ 每一条判据行都要把档位名印出来 —— 用户不该以为 15 条都是引擎硬要求）。
IMPACT_LEGEND: Mapping[str, str] = {
    IMPACT_SILENT: "引擎硬要求：名字对不上 = **静默失效**（模型照显示，功能不生效）",
    IMPACT_LOOKUP: "解析链要求：对不上 = 取错件 / 调试认错",
    IMPACT_CONVENTION: "**实测 100% 的约定**（引擎不按名字找它）：违反了值得当场知道，"
    "但不是引擎硬要求",
}


def impact_words(impact: str) -> str:
    """档位名 -> 一句人话（缺档位就原样回名字，⛔ 不静默）。"""
    return IMPACT_LEGEND.get(str(impact), f"（未登记的一档：{impact}）")


@dataclass(frozen=True, slots=True)
class ContractRule:
    """一条节点命名契约（谁在读 + 匹配规则 + 实测计数）。"""

    key: str
    #: 在哪一级比（**产品标识**：容器/节点名，⛔ 不许写方位词）
    level: str
    #: 引擎的精确条件（抄自反编译/实测；出处见 evidence）
    engine: str
    readers: tuple[str, ...]
    evidence: tuple[str, ...]
    hard: bool
    impact: str
    #: 不满足会怎样
    if_miss: str
    #: 285 件实测计数（进判据一行）
    measured: str = ""


RULES: tuple[ContractRule, ...] = (
    ContractRule(
        key="unit_root.name",
        level="prefab 顶层节点",
        engine="UnitBuilder.ModelName / Addressables 按地址取到的那颗 prefab 的根名 == 单位名",
        readers=("UnitBuilder.CreateUnitModel", "IMemoryContext.GetOrLoadAssetAsync"),
        evidence=("研究-断箭资产制作-方法与工具.md §7②", "_work/l7/scan_285.py"),
        hard=True,
        impact=IMPACT_LOOKUP,
        if_miss="按地址取到的 prefab 与单位名对不上 ⇒ 调试时认错件（游戏侧不抛）",
        measured="285/285 顶层名 == 地址末段",
    ),
    ContractRule(
        key="root.present",
        level="任意层（引擎只比名字）",
        engine='BoneContainer.Bake: x.name.ToLower() == "root"',
        readers=("BoneContainer.Bake+<>c.<Bake>b__23_0",),
        evidence=("unit_script_shapes.py 模块头", "unit_shape.py::read_unit_shape"),
        hard=True,
        impact=IMPACT_SILENT,
        if_miss="骨骼容器取不到根 ⇒ 轮子/悬挂/履带整族不挂（静默）",
        measured="285/285",
    ),
    ContractRule(
        key="body.present",
        level="任意层（引擎只比名字）",
        engine='BoneContainer.Bake: x.name.ToLower() == "body"',
        readers=("BoneContainer.Bake+<>c.<Bake>b__23_1",),
        evidence=("unit_script_shapes.py 模块头", "unit_shape.py::read_unit_shape"),
        hard=True,
        impact=IMPACT_SILENT,
        if_miss="车身骨骼容器取不到 ⇒ 车体不晃/后坐不作用（静默）",
        measured="285/285（US_LAV_AT-M_body 那颗是 `Body`，引擎 ToLower 后照样命中）",
    ),
    ContractRule(
        key="chassis.case",
        level="任意层；引擎在**根的直接子节点**里找（实测 278/287 在深度 1）",
        engine='UnitBuilder.InitArmors: Transform.FindChild(root, "Chassis")'
        " = op_Equality（大小写敏感）",
        readers=("UnitBuilder.InitArmors", "UnitBuilder.InitUnitExtraModelByName"),
        evidence=(
            "prefab_build.UNIT_NODE_CONTRACT['Chassis']",
            "UnitBuilder.txt:26402/25519 -> 0x18550C730 FindChild",
        ),
        hard=True,
        impact=IMPACT_SILENT,
        if_miss="命不中 ⇒ 护甲 / 附加模型 / 机动都不切换（引擎走 Enumerable.Empty 分支，不抛）",
        measured="逐字 Chassis 287 个 / 大小写变体 0 个",
    ),
    ContractRule(
        key="base_armor.case",
        level="Chassis 的直接子节点",
        engine='UnitBuilder.InitArmors: Assets.FindChildren(chassis, "BaseArmor") = op_Equality',
        readers=("UnitBuilder.InitArmors",),
        evidence=("UnitBuilder.txt:26587-26590", "Assets.txt:628-745 FindChildren"),
        hard=True,
        impact=IMPACT_SILENT,
        if_miss="FindChildren 返回空数组 ⇒ 护甲不切（静默）",
        measured="逐字 106 个 / 变体 0 个",
    ),
    ContractRule(
        key="base_armor.under_chassis",
        level="Chassis 的直接子节点",
        engine="FindChildren 只遍历**直接子节点**（不是递归）",
        readers=("UnitBuilder.InitArmors",),
        evidence=("prefab_build.UNIT_NODE_CONTRACT['BaseArmor'].note",),
        hard=True,
        impact=IMPACT_SILENT,
        if_miss="挂错一级（挂到 root 下）⇒ 与挂错名字同一条静默路径",
        measured="135/135 直接父名逐字 == Chassis",
    ),
    ContractRule(
        key="base_ability.case",
        level="Chassis 的直接子节点",
        engine='UnitBuilder.InitAbilities -> InitUnitExtraModelByName("BaseAbility")',
        readers=("UnitBuilder.InitAbilities",),
        evidence=("UnitBuilder.txt:24765/25183-25229",),
        hard=True,
        impact=IMPACT_SILENT,
        if_miss="同上（空数组、静默）",
        measured="逐字 23 个 / 变体 0 个",
    ),
    ContractRule(
        key="base_ability.under_chassis",
        level="Chassis 的直接子节点",
        engine="同 base_armor.under_chassis",
        readers=("UnitBuilder.InitAbilities",),
        evidence=("UnitBuilder.txt:25183/25223/25229",),
        hard=True,
        impact=IMPACT_SILENT,
        if_miss="同上",
        measured="135/135",
    ),
    ContractRule(
        key="base_mobility.case",
        level="Chassis 的直接子节点",
        engine='UnitBuilder.InitMobility -> InitUnitExtraModelByName("BaseMobility")',
        readers=("UnitBuilder.InitMobility",),
        evidence=("UnitBuilder.txt:25129/25154",),
        hard=True,
        impact=IMPACT_SILENT,
        if_miss="同上（空数组、静默）",
        measured="逐字 6 个 / 变体 0 个",
    ),
    ContractRule(
        key="base_mobility.under_chassis",
        level="Chassis 的直接子节点",
        engine="同 base_armor.under_chassis",
        readers=("UnitBuilder.InitMobility",),
        evidence=("UnitBuilder.txt:25183/25223/25229",),
        hard=True,
        impact=IMPACT_SILENT,
        if_miss="同上",
        measured="135/135",
    ),
    ContractRule(
        key="wheel.case",
        level="任意层（Wheels 遍历直接子节点）",
        engine='Wheels..ctor: name.Length>3 && name.Substring(0,3) == "LR0"/"RR0"'
        "（字面量、大小写敏感）",
        readers=("Wheels+<>c.<.ctor>b__10_0", "Wheels+<>c.<.ctor>b__10_1"),
        evidence=("Wheels_NestedType___c.txt:143/229", "unit_shape.py::wheel_side"),
        hard=True,
        impact=IMPACT_SILENT,
        if_miss="轮子不进 Wheels 名单 ⇒ 轮子不转（静默）",
        measured="命中 4268+38=4306 个（含 4 位序号的 38 个）；大小写近亲违规 0 个"
        "（其中 4 个长 1~2 的名字如 R1 两边都不中 ⇒ 引擎本来也不认，不算违规）",
    ),
    ContractRule(
        key="suspension.case",
        level="任意层（Suspension 遍历直接子节点）",
        engine='Suspension..ctor: name.Length>3 && name.Substring(0,2) == "L0"/"R0"（字面量）',
        readers=("Suspension+<>c.<.ctor>b__2_0", "Suspension+<>c.<.ctor>b__2_2"),
        evidence=("Suspension_NestedType___c.txt:67-151/179+", "unit_shape.py::suspension_side"),
        hard=True,
        impact=IMPACT_SILENT,
        if_miss="悬挂不进名单 ⇒ 车体不随地形起伏（静默）",
        measured="命中 4268+38 里的 L0/R0 那一半；大小写近亲违规 0 个",
    ),
    ContractRule(
        key="skinned.index_digits",
        level="任意层",
        engine="资产侧命名约定（引擎不按名字找 LOD 替身，靠 LODGroup 的 PPtr）",
        readers=("（无）",),
        evidence=(
            "研究-断箭资产制作-方法与工具.md §7②"
            " 的 `<单位>/Chassis/<状态>/<NNN>_skinned_<状态>`",
        ),
        hard=True,
        impact=IMPACT_CONVENTION,
        if_miss="LOD 替身的序号位数不齐 ⇒ 与同族对不上（**观感/工具链**问题，不是静默失效）",
        measured="1469/1469 都是三位",
    ),
    ContractRule(
        key="component.UnitPrefabRoot_on_root",
        level="prefab 顶层节点",
        engine="UnitBuilder 从 prefab 根取 UnitPrefabRoot（出厂闸 A1 同源）",
        readers=("UnitBuilder.CreateUnitModel",),
        evidence=("unit_bundle.verify_unit_bundle 的 A1",),
        hard=True,
        impact=IMPACT_SILENT,
        if_miss="根上没有它 ⇒ 皮肤/渲染器名单整块取不到",
        measured="285/285 都在根节点上",
    ),
    ContractRule(
        key="component.AnimationManager_on_root",
        level="prefab 顶层节点",
        engine="AnimationManager 由 UnitBuilder 在根上取（挂上才写轮/悬挂/履带参数）",
        readers=("UnitBuilder.CreateUnitModel", "AnimationManager"),
        evidence=("_work/dig/anim/params.tsv（283 件逐件取值）", "本模块 285 件实测"),
        hard=True,
        impact=IMPACT_SILENT,
        if_miss="挂错节点 ⇒ 那一件没有轮速/履带参数（静默）",
        measured="283/283 有它的件都在根节点上（另 2 件没有它）",
    ),
)

#: 会红的那些 key（用例与判据一行共用一处）。
HARD_KEYS: tuple[str, ...] = tuple(rule.key for rule in RULES if rule.hard)

_RULE_BY_KEY = {rule.key: rule for rule in RULES}

#: `_skinned_` 替身：`<NNN>_skinned_<状态>`（NNN 是三位序号）。
SKINNED_RE = re.compile(r"^(?P<index>\d+)_skinned_(?P<state>.+)$")
#: 判「大小写近亲」的两个既有匹配器（⛔ **不另造一套** —— 直接用 `unit_shape` 那两个）。
#:
#: 宽松口径 = 把名字**整体大写**再喂给匹配器（引擎比的是字面量 "LR0"/"RR0"/"L0"/"R0"，
#: 大写之后一定命中）；严格口径 = 用**原样**名字喂同一个匹配器。
#: 宽松中、严格不中 ⇒ 这条名字本来会被引擎认出来，只因大小写丢了 ⇒ 违规。
#: ⚠ 这样连「长度必须 > 3」那条边界也一起继承了：`R1`（长 2）两边都不中 ⇒ **不算违规**
#: （实测真件 RU_BUKM2 就有 `R1`）。
CASE_MATCHERS: tuple[tuple[str, str, Any], ...] = (
    ("wheel.case", "Wheels（LR0 / RR0 前缀 + 长度 > 3）", unit_shape.wheel_side),
    ("suspension.case", "Suspension（L0 / R0 前缀 + 长度 > 3）", unit_shape.suspension_side),
)
#: Base* 族：宽松（小写）-> 严格（逐字）。
BASE_NAMES: tuple[tuple[str, str, str], ...] = (
    ("base_armor", "basearmor", "BaseArmor"),
    ("base_ability", "baseability", "BaseAbility"),
    ("base_mobility", "basemobility", "BaseMobility"),
)


@dataclass(frozen=True, slots=True)
class Violation:
    """一条**点名到级**的违规：规则 ＋ **档位** ＋ 在哪一级 ＋ 具体哪个节点。

    `impact` 三档（`silent_failure` / `lookup` / `convention`）**每次都印出来** ——
    15 条硬规则里只有前两档是引擎硬要求，第三档是实测约定，⛔ 不该让用户以为它们是同一件事。
    """

    rule: str
    level: str
    path: str
    detail: str
    hard: bool = True
    impact: str = IMPACT_SILENT

    def line(self) -> str:
        mark = "★" if self.hard else "·"
        return (
            f"{mark} [{self.rule}·{self.impact}] {self.level}：{self.path} —— {self.detail}"
        )


@dataclass(frozen=True, slots=True)
class NodeContractReport:
    """一棵树对节点契约的判定结果（`violations` 里凡是 `hard` 的 ⇒ **别装机**）。"""

    name: str
    nodes: int
    #: 规则 key -> 命中数（严格形态命中几个节点；存在性规则命中 0/1）
    hits: Mapping[str, int] = field(default_factory=dict)
    violations: tuple[Violation, ...] = ()

    @property
    def hard_violations(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.hard)

    @property
    def ok(self) -> bool:
        return not self.hard_violations

    def problems(self) -> tuple[str, ...]:
        """进 `check-model` 的 `problems` 那一列（每条一行，带规则 key）。"""
        return tuple(v.line() for v in self.hard_violations)

    def hit_lines(self) -> tuple[str, ...]:
        """逐条**契约命中读数**（全绿时也要看得见每一条的计数）。

        标记口径：

        * `✗` —— 这条**报了违规**（点名在下几行）；
        * `✔` —— 命中了至少一个（严格形态命中）；
        * `·` —— 本件**没有这一类节点**（条件契约：真件里也只有 106/285 件有 BaseArmor）
          —— 那不是故障，⛔ 不许画成红的。
        """
        fired = {v.rule for v in self.violations}
        out = []
        for rule in RULES:
            got = int(self.hits.get(rule.key, 0))
            mark = "✗" if rule.key in fired else ("✔" if got else "·")
            out.append(
                f"   {mark} {rule.key}·{rule.impact}：命中 {got}（{rule.level}）"
                + (f" ［{rule.measured}］" if rule.measured else "")
            )
        return tuple(out)

    def lines(self) -> tuple[str, ...]:
        head = f"节点契约（{self.name}）：{self.nodes} 个节点 · 硬规则 {len(HARD_KEYS)} 条 · "
        head += (
            "全绿 ✔" if self.ok
            else f"**有红 {len(self.hard_violations)} 条**"
        )
        fired = sorted({v.impact for v in self.violations})
        legend = (
            "   档位：" + " · ".join(f"{name} = {impact_words(name)}" for name in fired)
            if fired
            else "   档位：silent_failure = 引擎硬要求 · lookup = 解析链 · convention = 实测约定"
        )
        return (
            head,
            legend,
            *self.hit_lines(),
            *(v.line() for v in self.violations),
        )


# --------------------------------------------------------------------------- #
# 归一化：任何树 -> 本模块吃的节点序列
# --------------------------------------------------------------------------- #
def _components_of(raw: Mapping[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    for comp in raw.get("components") or ():  # type: ignore[union-attr]
        if isinstance(comp, Mapping):
            out.append(str(comp.get("kind") or ""))
        else:
            out.append(str(comp))
    return tuple(k for k in out if k)


def normalized(nodes: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """把输入收成**一种形状**：`{name, index, parent_index, path, kinds}`。

    认三种输入：

    1. `unitypy_source.read_prefab_nodes` 的归一化节点（有 `transform_id` / `parent_transform`）；
    2. `prefab.PrefabTree.nodes`（`Node` 有 `transform_id` / `parent_id`）；
    3. 冻结抽件那一行（有 `path` / `parent` / `kinds`，没有 id）—— 见 `frozen_entry_to_nodes`。

    ⚠ 第 3 种是**路径**当键：同一层可以有**重名兄弟**，所以按出现顺序取**紧邻**的那一个父亲。
    """
    raw = [dict(n) for n in nodes]
    out: list[dict[str, Any]] = []
    by_id: dict[int, int] = {}
    by_path: dict[str, int] = {}
    for index, item in enumerate(raw):
        tid = item.get("transform_id")
        if tid is None:
            tid = item.get("path_id")
        if tid is not None:
            by_id[int(tid)] = index
        out.append(
            {
                "name": str(item.get("name") or ""),
                "index": index,
                # 给了 path 就用它（冻结抽件那条）；没给就在第 3 趟按父链拼出来
                "path": str(item.get("path") or ""),
                "kinds": tuple(
                    str(k) for k in (item.get("kinds") or _components_of(item))
                ),
            }
        )

    for index, item in enumerate(raw):
        parent = item.get("parent_transform")
        if parent is None:
            parent = item.get("parent_id")
        parent_index: int | None = None
        if parent is not None:
            parent_index = by_id.get(int(parent))
        elif "parent" in item:
            # 冻结抽件的路径形态：父亲 = 同一路径前缀里**最近**出现过的那一个
            text = str(item.get("parent") or "")
            if text:
                parent_index = by_path.get(text)
        out[index]["parent_index"] = parent_index
        if out[index]["path"]:
            by_path[out[index]["path"]] = index

    # 第 3 趟：没有 path 的（归一化节点 / PrefabTree 之外那两种输入）按父链拼一个，
    # 同名兄弟加 `#2` 区分 —— 判据一行里要能直接抄着去找节点，所以路径必须真能走。
    used: dict[str, int] = {}
    for rec in out:
        parent_index = rec.get("parent_index")
        if not rec["path"]:
            head = "" if parent_index is None else out[int(parent_index)]["path"] + "/"
            rec["path"] = head + rec["name"]
        seen = used.get(rec["path"], 0) + 1
        used[rec["path"]] = seen
        if seen > 1:
            rec["path"] = f"{rec['path']}#{seen}"
    return tuple(out)


def frozen_entry_to_nodes(entry: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """冻结抽件（`_work/l7/units_roots.json` 的一行）-> 本模块吃的节点序列。"""
    return normalized([dict(n) for n in (entry.get("nodes") or ())])


class _Index:
    """一棵树的查表（名字 / 小写名 / 父子 / 路径）。"""

    def __init__(self, nodes: Sequence[Mapping[str, Any]], name: str) -> None:
        self.nodes = list(nodes)
        self.name = name
        self.by_lower: dict[str, list[int]] = {}
        self.parent: dict[int, int | None] = {}
        self.root: int | None = None
        for i, n in enumerate(self.nodes):
            self.by_lower.setdefault(str(n["name"]).lower(), []).append(i)
            p = n.get("parent_index")
            self.parent[i] = None if p is None else int(p)
            if p is None and self.root is None:
                self.root = i

    def path_of(self, index: int) -> str:
        return str(self.nodes[index]["path"])

    def parent_name(self, index: int) -> str:
        p = self.parent.get(index)
        return "<根>" if p is None else str(self.nodes[p]["name"])

    def level_of(self, index: int) -> str:
        """这一级的产品标识：`<父名> 的直接子节点`（根那一个写 `prefab 顶层节点`）。"""
        p = self.parent.get(index)
        if p is None:
            return "prefab 顶层节点"
        return f"{self.nodes[p]['name']} 的直接子节点"

    def is_descendant_of(self, index: int, ancestor: int) -> bool:
        cursor: int | None = index
        seen = 0
        while cursor is not None and seen <= len(self.nodes):
            if cursor == ancestor:
                return True
            cursor = self.parent.get(cursor)
            seen += 1
        return False


def _add(out: list[Violation], rule: str, level: str, path: str, detail: str, hard: bool) -> None:
    """记一条违规（**档位从规则表里取**，⛔ 不许在这里写死）。"""
    out.append(
        Violation(
            rule=rule,
            level=level,
            path=path,
            detail=detail,
            hard=hard,
            impact=_RULE_BY_KEY[rule].impact,
        )
    )


def check_nodes(
    nodes: Iterable[Mapping[str, Any]],
    *,
    name: str = "",
    expected_root_name: str | None = None,
    scope: str = "unit",
) -> NodeContractReport:
    """**纯函数**：一棵树 -> 节点契约判定（`report.ok` 为假 = 别装机）。

    :param expected_root_name: 期望的顶层节点名（= 单位名，通常来自资产地址的末段）。
        给了就判 `unit_root.name`（那条**只在能算出期望值时才判** —— 本模块不猜单位名）。
    """
    tree = normalized(nodes)
    index = _Index(tree, name or (str(tree[0]["name"]) if tree else ""))
    hits: dict[str, int] = {}
    bad: list[Violation] = []

    def bump(key: str, n: int = 1) -> None:
        hits[key] = hits.get(key, 0) + n

    # ---- ① 顶层名 == 单位名（给了期望值才判）----
    #: ★ 2026-10-27：改成**大小写不敏感**（和 ② 的 root/body 同口径）。
    #: 依据（A 级）：`check-model` 的期望值来自包内**容器地址**末段，而 Unity 6000 写包时
    #: `m_PathFlags=7` ⇒ 容器地址被存成**小写**（见 `unit_bundle.py` 那段注释），prefab 顶层名却是
    #: 资产原本的大小写（`RU_T72B3A` vs `ru_t72b3a`）。
    #: 实测：T-72B3 与 T-72B3A 两颗包**用户已真机验收无问题**，却被这条判成红 ⇒ 红是**容器名小写**
    #: 造成的假阳性，不是 prefab 缺陷。基线 285/285 量的是资产原名，也符合本口径。
    #: ⛔ 只在大小写上放宽：名字真的不一样（例 `Body` vs `us_m113a3`）照样红。
    rule = _RULE_BY_KEY["unit_root.name"]
    if expected_root_name is not None and index.root is not None:
        root_name = str(tree[index.root]["name"])
        if root_name.strip().lower() == expected_root_name.strip().lower():
            bump(rule.key)
        else:
            _add(bad, rule.key, rule.level, index.path_of(index.root),
                 f"顶层名是 {root_name!r}，单位名（资产末段）是 {expected_root_name!r}"
                 " —— 引擎按名字取根，对不上就认错件", rule.hard)

    # ---- ② root / body：ToLower 之后全等（**大小写不敏感**）----
    for key, want in (("root.present", "root"), ("body.present", "body")):
        rule = _RULE_BY_KEY[key]
        got = index.by_lower.get(want, [])
        if got:
            bump(rule.key, len(got))
        else:
            _add(bad, rule.key, rule.level, index.name,
                 f"整棵树里没有 ToLower()=={want!r} 的节点（实有顶层：{list(index.by_lower)[:6]}…）"
                 f" —— {rule.engine}", rule.hard)

    # ---- ③ Chassis：宽松（小写）能命中就必须逐字 ----
    rule = _RULE_BY_KEY["chassis.case"]
    for i in index.by_lower.get("chassis", []):
        if scope != "unit":
            break
        actual = str(tree[i]["name"])
        if actual == "Chassis":
            bump(rule.key)
        else:
            _add(bad, rule.key, index.level_of(i), index.path_of(i),
                 f"名字是 {actual!r}，引擎要的是逐字 'Chassis'（op_Equality，大小写敏感）"
                 " ⇒ 护甲/附加模型/机动都不切换（静默）", rule.hard)

    # ---- ④ Base*：逐字 + 必须挂在逐字 `Chassis` 的直接子一级 ----
    for key, lower, exact in BASE_NAMES:
        if scope != "unit":
            break
        case_rule = _RULE_BY_KEY[f"{key}.case"]
        level_rule = _RULE_BY_KEY[f"{key}.under_chassis"]
        for i in index.by_lower.get(lower, []):
            actual = str(tree[i]["name"])
            if actual == exact:
                bump(case_rule.key)
            else:
                _add(bad, case_rule.key, index.level_of(i), index.path_of(i),
                     f"名字是 {actual!r}，引擎要的是逐字 {exact!r}（FindChildren + op_Equality）"
                     " ⇒ 护甲/能力/机动整族不切（静默）", case_rule.hard)
            if index.parent_name(i) == "Chassis":
                bump(level_rule.key)
            else:
                _add(bad, level_rule.key, index.level_of(i), index.path_of(i),
                     f"挂在 {index.parent_name(i)!r} 下；引擎是 FindChild('Chassis')"
                     " -> FindChildren（**只遍历直接子节点**）"
                     "⇒ 挂错一级与挂错名字同一条静默路径", level_rule.hard)

    # ---- ⑤ 轮 / 悬挂：引擎比的是字面量 LR0/RR0/L0/R0（大小写敏感）----
    #
    # 尺子 = `unit_shape` 那两个匹配器（**同一份逻辑**），只是喂两种口径的名字：
    #   宽松 = name.upper()（大写之后一定命中）  ·  严格 = name 原样
    # ⚠ **只判大小写**：位数不齐不算违规 —— 实测真件里有 `R1`（长 2，RU_BUKM2）与
    #   `LR0010`（4 位，RU_DT30PM）共 42 个，它们两边都不中 ⇒ 不算违规（引擎自己也不认它们）。
    for key, why, matcher in CASE_MATCHERS:
        if scope != "unit":
            break
        rule = _RULE_BY_KEY[key]
        for i, node in enumerate(tree):
            text = str(node["name"])
            if matcher(text) is not None:
                bump(rule.key)
            elif matcher(text.upper()) is not None:
                _add(bad, rule.key, index.level_of(i), index.path_of(i),
                     f"名字是 {text!r}；{why} 比的是**字面量**（大小写敏感）"
                     " ⇒ 这一颗不进名单（轮子不转 / 车体不随地形，静默）", rule.hard)

    # ---- ⑥ `NNN_skinned_<状态>`：序号三位 ----
    rule = _RULE_BY_KEY["skinned.index_digits"]
    for i, node in enumerate(tree):
        text = str(node["name"])
        hit = SKINNED_RE.match(text)
        if not hit:
            continue
        if len(hit.group("index")) == 3:
            bump(rule.key)
        else:
            _add(bad, rule.key, index.level_of(i), index.path_of(i),
                 f"名字是 {text!r}，序号 {hit.group('index')!r} 是 {len(hit.group('index'))} 位；"
                 "真件 1469/1469 都是三位（<NNN>_skinned_<状态>）", rule.hard)

    # ---- ⑦ 组件挂载级（加组件那一半的闸）----
    for key, cls, want_root in (
        ("component.UnitPrefabRoot_on_root", "MB:UnitPrefabRoot", True),
        ("component.AnimationManager_on_root", "MB:AnimationManager", True),
    ):
        rule = _RULE_BY_KEY[key]
        if scope != "unit" and key == "component.UnitPrefabRoot_on_root":
            continue
        for i, node in enumerate(tree):
            if cls not in node["kinds"]:
                continue
            is_root = index.parent.get(i) is None
            if is_root == want_root:
                bump(rule.key)
            else:
                _add(bad, rule.key, index.level_of(i), index.path_of(i),
                     f"{cls} 挂在 {index.parent_name(i)!r} 上；它必须挂在**根节点**上", rule.hard)

    return NodeContractReport(
        name=index.name, nodes=len(tree), hits=dict(hits), violations=tuple(bad)
    )


def check_tree(
    tree: Any,
    *,
    name: str = "",
    expected_root_name: str | None = None,
    scope: str = "unit",
) -> NodeContractReport:
    """任何有 `.nodes` 的树（`prefab.PrefabTree`）-> 判定；其余对象原样转 `check_nodes`。"""
    inner = getattr(tree, "nodes", None)
    if inner is None:
        return check_nodes(
            tree, name=name, expected_root_name=expected_root_name, scope=scope
        )
    rows: list[dict[str, Any]] = []
    for node in inner:
        rows.append(
            {
                "name": node.name,
                "transform_id": node.transform_id,
                "parent_transform": node.parent_id,
                "path": node.path,
                "kinds": tuple(getattr(node, "kinds", ()) or ()),
            }
        )
    return check_nodes(rows, name=name, expected_root_name=expected_root_name, scope=scope)
