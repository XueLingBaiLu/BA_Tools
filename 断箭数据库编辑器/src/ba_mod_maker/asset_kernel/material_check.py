r"""材质级判据 **M1–M8**（设计稿 `docs/设计-材质完全自定义.md` §6）· 产品侧可跑的检查器。

## 这一层做什么

设计稿 §6 那八条判据，原来是**散在文档里的一段文字**（"每条都要能红"只是写在表里）。
本模块把它们变成**能跑的函数**：每条一个 `CheckResult`（`ok` ＋ 读数行 ＋ 提示），
`run_all` 汇总成一份 `MaterialCheckReport`，逐行印出来。

| # | 判据（设计稿 §6 的机械形式） | 红了表示 |
|---|---|---|
| **M1** | 新材质读回：pathID · `m_Name` · 颜色槽 · VT 栈**逐字符相同** | 写回没落对 / VT 被动过 |
| **M2** | 只加 `Skins.Data` ⇒ 玩家菜单里看不到（两侧 Id 不一致） | 两侧**同时**改了 |
| **M3** | 成套检查：底图车族 与 VT `itemName` 车族一致 | 跨族沿用（NCC 0.0245） |
| **M4** | 删材质前引用普查**必须为空** | 还有别的引用（履带料 refs=9） |
| **M5** | `m_TextureFormat` 必须在**可编码集**里 | 喂了 BC5/ASTC/Crunched（能力不足**必须报**） |
| **M6** | 包级 `check-model` 全绿 **且**新对象在 `m_PreloadTable` 里 | 少了预载 / 包级有问题 |
| **M7** | 文案里 `换成/替换/swap/replace` **0 命中** | 冒出了「换」这个复合动作 |
| **M8** | 只出机械读数（覆盖率 / 槽位 / 门控 / **降级清单**） | 混进主观词，或缺降级理由 |

## 口径（三条，别在这里发明）

1. **本模块只判、不读盘**：每一项输入都是"已经从真件 / 计划里读出来的读数"（路径、pathID、
   槽名、格式号、引用清单…）。读盘那一步在各自的链上（`prefab_skins` / `renderer_slots` /
   `texture_build` / `unit_bundle`）—— 判据与读取分开，尺子才能单独喂正控。
2. **可编码集只有一份**：`m_TextureFormat` 那一格问 `textures.ENCODABLE_FORMATS`，
   ⛔ 不在这里抄第二份（抄一份就会有一天两边不一样）。
3. **M4 复用** `renderer_slots.MaterialCensus`（整包 PPtr 普查那把尺子）—— 它是"谁指着这颗材质"
   的唯一入口；本模块只判 `refs` 空不空。

## 能红吗（每条都有正控 ＋ 负控）

用例 `tests/test_material_check.py` 里 M1–M8 **各一条正控 ＋ 各一条负控**：
正控 = 合规输入 ⇒ `ok is True`；负控 = 把那一处改坏 ⇒ `ok is False` 且读数里点出是哪一处。
`run_all` 的汇总也有一条正控（全合规 ⇒ `report.ok`）与一条负控（塞一条坏的 ⇒ 它必须报出来）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CheckInput",
    "CheckResult",
    "MaterialCheckReport",
    "encodable_formats",
    "m1_new_material_readback",
    "m2_visibility_pair",
    "m3_family_match",
    "m4_deletable_material",
    "m5_texture_format",
    "m6_bundle_gate",
    "m7_no_swap_wording",
    "m8_mechanical_readings",
    "run_all",
]

#: M7 那把尺子的词表 —— ⛔ 只加不减（AGENTS §0.10：产品里不许有「换」这个复合动作）。
#: 大小写不敏感；中文那三个是用户原话点名禁的（"换成 …"）。
SWAP_WORDS = ("换成", "替换", "换上", "swap", "replace")

#: M8 的**主观词**黑名单：材质级读数里出现它们 = 我在拿"像不像"当判据（AGENTS §0.12.1）。
#: ⛔ 这三类只有用户能判：遮蔽关系 / 倒影水感 / 条纹花纹落到模型上。
SUBJECTIVE_WORDS = ("看起来", "像不像", "像游戏", "好看", "更难看了")


def encodable_formats() -> tuple[int, ...]:
    """可编码的 `m_TextureFormat` —— **口径只有一份**（`textures.ENCODABLE_FORMATS`）。"""
    from ba_mod_maker.asset_kernel import textures  # noqa: PLC0415 -- 拆环：只有 M5 要它

    return tuple(int(v) for v in textures.ENCODABLE_FORMATS)


# --------------------------------------------------------------------------- #
# 结果形状
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class CheckResult:
    """一条判据的读数：`ok` ＋ 逐行读数 ＋（红了时的）下一步提示。"""

    key: str
    title: str
    ok: bool
    readings: tuple[str, ...] = ()
    hint: str = ""
    skipped: bool = False

    def line(self) -> str:
        mark = "跳过" if self.skipped else ("✅" if self.ok else "❌")
        head = f"{mark} {self.key} {self.title}"
        return f"{head}：{self.readings[0]}" if self.readings else head

    def lines(self) -> tuple[str, ...]:
        out = [self.line()]
        out.extend(f"    {item}" for item in self.readings[1:])
        if not self.ok and self.hint:
            out.append(f"    ⇒ {self.hint}")
        return tuple(out)


@dataclass(frozen=True, slots=True)
class MaterialCheckReport:
    """八条判据的汇总（`ok` = 没有一条红；跳过的**不算红**但如实列出来）。"""

    results: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        return all(item.ok or item.skipped for item in self.results)

    @property
    def failed(self) -> tuple[CheckResult, ...]:
        return tuple(item for item in self.results if not item.ok and not item.skipped)

    @property
    def skipped(self) -> tuple[CheckResult, ...]:
        return tuple(item for item in self.results if item.skipped)

    def lines(self) -> tuple[str, ...]:
        head = (
            f"材质级判据 M1–M8：{'全绿' if self.ok else '有红'}"
            f"（{len(self.results) - len(self.skipped)} 条跑了 / {len(self.skipped)} 条跳过）"
        )
        out = [head]
        for item in self.results:
            out.extend(item.lines())
        return tuple(out)


# --------------------------------------------------------------------------- #
# M1 · 新材质读回
# --------------------------------------------------------------------------- #
def m1_new_material_readback(
    *,
    path_id: int,
    object_ids: Iterable[int],
    name: str,
    expected_name: str,
    color_slot: str,
    color_texture_id: int,
    expected_texture_id: int,
    stacks_before: str,
    stacks_after: str,
    slot_pid_inside: bool = True,
) -> CheckResult:
    """**M1**：新增材质写回后从包里读回来对四件事（pathID / m_Name / 颜色槽 / VT 栈）。

    `stacks_before` / `stacks_after` 是 `m_BuildTextureStacks` 的**文本形式**
    （调用方按自己的读法给，例如 `json.dumps(…, sort_keys=True)`）——
    判据是**逐字符相同**：新材质必须**沿用**原材质那套 VT 绑定（设计稿 §2.2：底图与 VT 必须成套）。
    """
    problems: list[str] = []
    ids = {int(value) for value in object_ids}
    if int(path_id) not in ids:
        problems.append(f"pathID={int(path_id)} 不在对象表里（{len(ids)} 个对象）")
    if name != expected_name:
        problems.append(f"m_Name={name!r} ≠ 期望 {expected_name!r}")
    if int(color_texture_id) != int(expected_texture_id):
        problems.append(
            f"颜色槽 {color_slot} 指向 {int(color_texture_id)} ≠ 新底图 {int(expected_texture_id)}"
        )
    if not slot_pid_inside:
        problems.append(f"颜色槽 {color_slot} 指向包外（悬空 PPtr）")
    if str(stacks_before) != str(stacks_after):
        problems.append(
            f"m_BuildTextureStacks 被改了：{stacks_before!r} → {stacks_after!r}"
            "（必须**逐字符相同** —— 底图与 VT 要成套）"
        )
    readings = (
        f"pathID={int(path_id)} 在对象表里 · m_Name={name!r} · 颜色槽 {color_slot}"
        f" -> {int(color_texture_id)} · VT 栈{'一致' if not problems else '**不一致**'}",
        f"VT 栈（原/新）：{stacks_before!r} / {stacks_after!r}",
    )
    return CheckResult(
        key="M1",
        title="新材质读回",
        ok=not problems,
        readings=(readings[0], *problems, readings[1]) if problems else readings,
        hint="写回没落对（先看 writer 的写后复核），或 VT 绑定被动过"
        " —— 后者会静默换掉整套法线/遮罩",
    )


# --------------------------------------------------------------------------- #
# M2 · 资产层与可见性侧
# --------------------------------------------------------------------------- #
def m2_visibility_pair(
    *, skin_ids: Iterable[int], menu_ids: Iterable[int], new_id: int
) -> CheckResult:
    """**M2**：新皮肤 Id 在 `Skins.Data` 里有、在玩家菜单那侧（`UserItemsConfig`）里**没有**。

    这正是设计稿 §5 那条判据：**资产层加一条 ⇒ 玩家菜单里看不到**（可见性由另一张表管）。
    两侧都有 ⇒ 这说明"只加资产层"这件事没做到（或者这根本不是新皮肤）。
    """
    asset = {int(v) for v in skin_ids}
    menu = {int(v) for v in menu_ids}
    target = int(new_id)
    problems: list[str] = []
    if target not in asset:
        problems.append(f"Id={target} 不在 Skins.Data 里（资产层没加）")
    if target in menu:
        problems.append(
            f"Id={target} **也在**玩家菜单那侧（UserItemsConfig）里"
            " —— 这次动作不是「只加资产层」"
        )
    readings = (
        f"Id={target}：Skins.Data {'有' if target in asset else '无'}"
        f"（共 {len(asset)} 条）/ 菜单侧 {'有' if target in menu else '无'}（共 {len(menu)} 条）",
        "判据是**两侧不一致**：资产层有、菜单侧没有 ⇒ 游戏里看不见（设计稿 §5）",
    )
    return CheckResult(
        key="M2",
        title="资产层新增 / 可见性侧没动",
        ok=not problems,
        readings=(readings[0], *problems) if problems else readings,
        hint="可见性是另一档（UserItemsConfig，写 data.unity3d ≈ 4 分钟）"
        " —— ⛔ 两件事不要混在一次改动里",
    )


# --------------------------------------------------------------------------- #
# M3 · 成套检查（底图车族 vs VT item 车族）
# --------------------------------------------------------------------------- #
def m3_family_match(
    *, texture_family: str, item_family: str, texture_name: str = "", item_name: str = ""
) -> CheckResult:
    """**M3**：底图的**车族**与 VT `itemName` 的**车族**必须一致。

    跨族沿用的现场（T-90M 的 item 配 T-72B 的底图）实测 **NCC 0.0245**，用户判
    「车体材质明显错乱、花纹完全变样」⇒ 这条必须能红。
    ⚠ 车族怎么认是调用方的事（按材质名 / owner 名单反查，见 `docs/设计-材质完全自定义.md` §2.2）。
    """
    same = str(texture_family).strip().lower() == str(item_family).strip().lower()
    readings = (
        f"底图 {texture_name or '?'} 车族 = {texture_family!r}"
        f" · VT {item_name or '?'} 车族 = {item_family!r}"
        f" ⇒ {'同族（对）' if same else '**跨族**'}",
        "跨族先例：T-90M 的 item 配 T-72B 的底图（NCC 0.0245 ⇒ 花纹完全变样）",
    )
    return CheckResult(
        key="M3",
        title="底图 / VT 成套（同族）",
        ok=same,
        readings=readings,
        hint="换一套 VT 就是换骨：同族的 355 套 item 里挑，或按设计稿 §2.6 路 A 自己烘一套",
    )


# --------------------------------------------------------------------------- #
# M4 · 删除前的引用普查必须为空
# --------------------------------------------------------------------------- #
def m4_deletable_material(
    *, refs: Iterable[Any], material_name: str = "", material_path_id: int = 0
) -> CheckResult:
    """**M4**：删材质前引用普查**必须为空**（`refs` = 谁指着它的清单）。

    `refs` 里每一项只要有 `label()` 或是个字符串就能报出来 —— 传
    `renderer_slots.census_material(...).refs` 就是那条真链接。
    """
    items = list(refs)
    labels = [str(item.label()) if hasattr(item, "label") else str(item) for item in items]
    readings = (
        f"材质 {material_name or '?'}（pathID={material_path_id}）被 {len(items)} 条引用指着"
        + ("" if not items else f"：{labels[:4]}"),
        "判据 = **0 条**：还有引用时删掉它，那些槽/皮肤表条目会指向一个不存在的资产",
    )
    return CheckResult(
        key="M4",
        title="删除前引用普查为空",
        ok=not items,
        readings=readings if not items else (readings[0], *readings[1:]),
        hint="共享面实测：履带料 refs=9（8 台车）—— 先把引用一并处理，或这次别删",
    )


# --------------------------------------------------------------------------- #
# M5 · m_TextureFormat 必须落在可编码集里
# --------------------------------------------------------------------------- #
def m5_texture_format(
    *, texture_format: int, encodable: Sequence[int] | None = None, texture_name: str = ""
) -> CheckResult:
    """**M5**：写回的 `m_TextureFormat` 必须落在可编码集里。

    ⚠ 2026-10-17（L5）口径变更：集从 {10, 12} 加宽到 **{10, 12, 25, 26}**
    （= BC1/BC3/BC7/BC4）—— BC7 与 BC4 现在**真能编**了（`asset_kernel/texture_encode.py`）。
    ⛔ 旧行为一条都没松：BC5（法线）/ ASTC / Crunched（28/29）照样**红**。
    """
    allowed = tuple(int(v) for v in (encodable if encodable is not None else encodable_formats()))
    ok = int(texture_format) in allowed
    readings = (
        f"{texture_name or '贴图'} 的 m_TextureFormat={int(texture_format)}"
        f" {'在' if ok else '**不在**'} 可编码集 {list(allowed)}"
        f" ⇒ {'可编' if ok else '**能力不足**（必须报，⛔ 不许静默写）'}",
        "BC5（法线）/ ASTC / Crunched（28/29）今天编不动 —— 那几档只能「原样搬运」，不能改像素",
    )
    return CheckResult(
        key="M5",
        title="m_TextureFormat 可编码",
        ok=ok,
        readings=readings,
        hint="先解成无压缩再编（D3 那条），或者这一版就别改像素",
    )


# --------------------------------------------------------------------------- #
# M6 · 包级全绿 ＋ 新对象在 m_PreloadTable 里
# --------------------------------------------------------------------------- #
def m6_bundle_gate(
    *,
    problems: Iterable[str],
    preload_ids: Iterable[int],
    new_path_ids: Iterable[int],
) -> CheckResult:
    """**M6**：包级 `check-model` 全绿（`problems` 空）**且**新对象在 `m_PreloadTable` 里。"""
    bad = [str(item) for item in problems]
    preload = {int(value) for value in preload_ids}
    wanted = [int(value) for value in new_path_ids]
    missing = [value for value in wanted if value not in preload]
    readings = (
        f"包级问题 {len(bad)} 条；新对象 {len(wanted)} 个，"
        f"没进 m_PreloadTable 的 {len(missing)} 个{'' if not missing else f'：{missing[:4]}'}",
        f"m_PreloadTable 共 {len(preload)} 条"
        + (f"；包级问题（前 3）：{bad[:3]}" if bad else ""),
    )
    return CheckResult(
        key="M6",
        title="包级全绿 ＋ 新对象进预载表",
        ok=not bad and not missing,
        readings=readings,
        hint="少一条预载 = 游戏侧取不到（W8 的 J3）；包级问题先按 check-model 的读数修",
    )


# --------------------------------------------------------------------------- #
# M7 · 文案里不许有「换」
# --------------------------------------------------------------------------- #
def m7_no_swap_wording(*, texts: Iterable[str]) -> CheckResult:
    r"""**M7**：产品文案（CLI / GUI 的 help 与提示）里 `换成\|替换\|swap\|replace` **0 命中**。"""
    hits: list[str] = []
    count = 0
    for text in texts:
        count += 1
        low = str(text).lower()
        for word in SWAP_WORDS:
            if word.lower() in low:
                hits.append(f"{word!r} @ {str(text)[:60]!r}")
    readings = (
        f"扫了 {count} 段文案，命中 {len(hits)} 处禁止词"
        + (f"：{hits[:3]}" if hits else "（0 命中）"),
        "⛔ 产品只有新增 / 删除 / 修改三种原子动作 ——「换」是复合动作（AGENTS §0.10）",
    )
    return CheckResult(
        key="M7",
        title="文案里没有「换」",
        ok=not hits,
        readings=readings,
        hint="把「换成 X」拆成「删除旧的 ＋ 新增新的」两句",
    )


# --------------------------------------------------------------------------- #
# M8 · 「像不像」不由我判：只出机械读数
# --------------------------------------------------------------------------- #
def m8_mechanical_readings(
    *,
    plans: Iterable[Any] = (),
    extra_lines: Iterable[str] = (),
    coverage_lines: Iterable[str] = (),
) -> CheckResult:
    """**M8**：材质级只出**机械**读数 + **降级清单**。

    * `plans` 是 `materials.MaterialPlan`（覆盖率 / 槽位 / 关键字门控都从它读）；
    * **非"完整"档必须写降级**：每个 `coverage != 完整` 的计划都要有 `notes`（降级理由）；
    * 读数里**不许出现主观词**（⛔ 遮蔽 / 倒影 / 花纹这类只有用户能判，AGENTS §0.12.1）。
    """
    items = list(plans)
    lines = [str(item) for item in (*coverage_lines, *extra_lines)]
    degradations: list[str] = []
    for plan in items:
        coverage = str(getattr(plan, "coverage", "") or "")
        name = str(getattr(plan, "material_name", "") or "?")
        notes = tuple(getattr(plan, "notes", ()) or ())
        lines.append(f"{name}：{coverage}")
        if coverage and coverage != "完整":
            if notes:
                degradations.append(f"{name}：{coverage} —— {notes[0]}")
            else:
                degradations.append(f"{name}：{coverage} —— **没写降级理由**")
    subjective = [
        f"{word} @ {line[:60]!r}"
        for line in lines
        for word in SUBJECTIVE_WORDS
        if word in line
    ]
    missing_reason = [item for item in degradations if "没写降级理由" in item]
    readings = (
        f"机械读数 {len(lines)} 行 · 降级清单 {len(degradations)} 条 · 主观词 {len(subjective)} 处",
        *degradations[:4],
        *(f"主观词：{subjective[:3]}" if subjective else ()),
        *(f"缺降级理由：{missing_reason[:3]}" if missing_reason else ()),
        "⛔ 遮蔽 / 倒影 / 条纹花纹三类只有用户能判（AGENTS §0.12.1）—— 它们不进这把尺子",
    )
    return CheckResult(
        key="M8",
        title="只出机械读数 ＋ 降级清单",
        ok=not subjective and not missing_reason,
        readings=readings,
        hint="把主观判断删掉（交给用户），或给非完整档补上降级理由",
    )


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class CheckInput:
    """`run_all` 的输入：**每一项都是已经读出来的读数**（没给的那条就"跳过"）。

    形状与各 `mX_*` 的关键字参数一一对应（键名就是参数名）。
    """

    m1: Mapping[str, Any] | None = None
    m2: Mapping[str, Any] | None = None
    m3: Mapping[str, Any] | None = None
    m4: Mapping[str, Any] | None = None
    m5: Mapping[str, Any] | None = None
    m6: Mapping[str, Any] | None = None
    m7: Mapping[str, Any] | None = None
    m8: Mapping[str, Any] | None = None
    extras: tuple[CheckResult, ...] = field(default_factory=tuple)


_CHECKS: Mapping[str, tuple[str, Any]] = {
    "m1": ("M1", m1_new_material_readback),
    "m2": ("M2", m2_visibility_pair),
    "m3": ("M3", m3_family_match),
    "m4": ("M4", m4_deletable_material),
    "m5": ("M5", m5_texture_format),
    "m6": ("M6", m6_bundle_gate),
    "m7": ("M7", m7_no_swap_wording),
    "m8": ("M8", m8_mechanical_readings),
}


def run_all(source: CheckInput) -> MaterialCheckReport:
    """跑一遍 M1–M8：给了读数的就判，没给的那条记"跳过"（⛔ 不当绿）。"""
    results: list[CheckResult] = []
    for name, (key, fn) in _CHECKS.items():
        payload = getattr(source, name)
        if payload is None:
            results.append(
                CheckResult(
                    key=key,
                    title="（没给读数）",
                    ok=True,
                    readings=("跳过：没给这一条的读数",),
                    skipped=True,
                )
            )
            continue
        results.append(fn(**dict(payload)))
    results.extend(source.extras)
    return MaterialCheckReport(results=tuple(results))
