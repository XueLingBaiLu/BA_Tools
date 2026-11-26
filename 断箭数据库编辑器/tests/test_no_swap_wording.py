r"""J3 尺子：操作文案面的「换」判据（⛔ 无「换」入口 · ADR 0008 决定 2）。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests\test_no_swap_wording.py -q`
-> 六处操作文案面**从 2 条红**（`skin_install.__doc__` 里两处「换成」）**变 0 条红**，
而负控（往**真 help** 与 ④⑤⑥ 的**真文本**里塞一句「换上」/「替换」）**照旧能红**。

## 尺子量哪六处（①②③ = Lead task-1 定；④⑤⑥ = task-2 扩面的**用户可见** sink）

| # | 面 | 取法 |
|---|---|---|
| ① | **CLI 全部子命令帮助文本** | `_build_parser()` 起，递归 `format_help()` 后拼接 |
| ② | **`skin_install` 模块头** | `skin_install.__doc__` |
| ③ | **`SkinSwapPlan.summary()`** | 真构造一个 plan（借 `_fake_plan`）再调 |
| ④ | **`SkinSwapPlan` 类文档串** | `SkinSwapPlan.__doc__` |
| ⑤ | **`criterion_lines()` 的输出**（CLI 会打印它） | 同一个 plan 的 `criterion_lines()` 逐行拼 |
| ⑥ | **`install_swap` 的 `patch_label`**（进补丁柜） | 假 writer 拦下 kwargs（不写盘） |

（① 是**递归**取的 —— 子命令里还能挂子命令，`_children` 就干这个；实测 21 份 / 13,465 字符。）

判定：这六处文本里 `换成|换上|替换|更换|swap|replace` **0 命中**。

## ⛔ 边界（不在范围内，别拿它当违规）

- **导航文案**：界面里「中栏换成那一行 / 右栏换成它的全部列」这类**视图切换** ——
  它说的是"这一栏显示哪一行"，不是资产操作（`app/` 82 个 .py 里「换成」**140 处**）；
- **标识符名**：`SkinSwapPlan` / `plan_swap` / `install_swap` / `--swap-yz`。
  `--swap-yz` 是 `build-model`（从 OBJ 造模型包）的**坐标轴**开关（"把 Z 朝上转成
  Unity 的 Y 朝上"）—— 参数名不在本刀范围内。尺子用 `_mask_code_tokens` 把它挡在外面，同时用
  `code_tokens_with_banned_stem` **把它印出来**：挡掉了什么是可见的，不是暗的；
- **`core/fields/field_labels.json`**：那是**游戏字段**的中文名
  （`Options.ReplaceUnitId` = "替换成另一个单位"），不是我们的操作；
- **`core/session.py` 的「原子替换」**：文件替换（`os.replace`），不是资产操作 ——
  `skin_install.restore()` 文档串里那句「一个字节都不替换」同属这一类，**没动**；
- **裸「换」**（不进六词尺子，Lead 2026-10-08 口径 (a)）：「不换像素」这类是正常中文，
  把裸「换」扫进去就得到一把**必须靠豁免才能绿**的尺子（AGENTS §0.9 动作 2）⇒
  点名的那几处改用**指名断言**钉住（`test_named_spots_state_modify_the_direction`）。

## 实测数字（2026-10-08，复算就是下面这一条命令）

    .venv\Scripts\python.exe -m pytest tests\test_no_swap_wording.py -q -k src_census -s -n 0
    -> [分母] 173 个文本文件 | 换成|替换 = 284 处 / 276 行

- 全仓 `src/` 里 `换成|替换` 命中 **284 处 / 276 行**（task-1 落地**前**是 286 / 278）；
- 其中落在**操作文案面**、本尺子盯着的（改完 **0**）：
  · **ADR 0008 背景 §5 / 设计稿 §7 第 4 步点名的 3 处**：`skin_install.py:1` 的「8 字节换图」·
    `SkinSwapPlan.summary()`(:447) 的 `A → B` · `cli/__main__.py:116` 的「材质贴图换图」；
  · **task-2 扩面的 3 处**（都是用户能看见的 sink）：`SkinSwapPlan` 类文档串「材质槽换图」·
    `criterion_lines()`(:431) 的 `[皮肤换图] … 旧(id) → 新(id)` ·
    `install_swap`(:529-531) 的 `patch_label`「皮肤：… 旧→新」；
- ⚠ 这 6 处里只有 `__doc__` 那两处「换成」是**六词尺子直接抓到**的（task-1 改前 2 条红）——
  其余的「换图」/ `A → B` / `A→B` 都不含六个禁词。所以另有一组**指名断言**盯着它们
 （`test_named_spots_state_modify_the_direction`）。这一节是**量出来的**，不是照抄设计稿：
  设计稿说的是"这些地方要改"，不是说"它们都命中六个禁词"。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import skin_install
from ba_mod_maker.cli import __main__ as cli_main
from test_skin_install import SLOT_A, _fake_plan

#: 六个禁词（ADR 0008 决定 2：「产品 CLI / GUI 文案里 换成|替换|swap|replace 0 命中」
#: ＋ Lead 2026-10-08 补的「换上 / 更换」）。
BANNED_ZH: tuple[str, ...] = ("换成", "换上", "替换", "更换")
BANNED_EN: tuple[str, ...] = ("swap", "replace")

#: 代码形状的记号：`--swap-yz`（选项名）/ `plan_swap`（下划线）/ `SkinSwapPlan`（驼峰）/
#: `os.replace`（点号链）—— 标识符名不在 J3 范围内（见文件头边界）。
_CODE_TOKEN = re.compile(
    r"--?[A-Za-z][A-Za-z0-9_-]*|[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
)
_ASCII_WORD = re.compile(r"[A-Za-z]+")
#: 分母口径（文件头那条命令量的就是它）。
_SRC_BANNED = re.compile("换成|替换")
SRC_SUFFIXES = frozenset({".py", ".json", ".md", ".txt", ".qss", ".toml", ".cfg"})


# --------------------------------------------------------------------------- #
# 尺子本体（纯函数，零 IO —— 先证明它能红，再拿它量产品）
# --------------------------------------------------------------------------- #
def _is_code_identifier(token: str) -> bool:
    """这个记号是不是"代码"而不是"散文"：选项名 / 含下划线 / 驼峰 / 点号链。

    `token.isupper()` 那半句是**为了别把纯大写散文也挡掉**（`SWAP` 该报就得报）。
    """
    if token.startswith("-") or "_" in token or "." in token:
        return True
    return any(ch.isupper() for ch in token[1:]) and not token.isupper()


def _mask_code_tokens(text: str) -> str:
    """把代码形状的记号抹成空格，只留散文 —— 标识符名不算违规（见文件头边界）。"""
    return _CODE_TOKEN.sub(
        lambda match: " " if _is_code_identifier(match.group(0)) else match.group(0), text
    )


def banned_hits(text: str) -> list[str]:
    r"""**纯函数**：这段文案命中几个禁词（命中几次报几次，中文在前、英文在后）。

    * 中文四词按**子串**找（中文里没有标识符，不会误伤）；
    * 英文两词先抹掉代码记号、再取 ASCII 字母串按**词干**比 ——
      `swapped` / `swapping` / `replacement` 都算；`贴图swap掉` 这种**中文贴着英文**
      也算（不靠 `\b`：Python 里汉字算 `\w`，`\b` 在那儿根本不出现）。
    """
    hits = [word for word in BANNED_ZH for _ in range(text.count(word))]
    for word in _ASCII_WORD.findall(_mask_code_tokens(text).casefold()):
        for banned in BANNED_EN:
            if word.startswith(banned):
                hits.append(banned)
                break
    return hits


def code_tokens_with_banned_stem(text: str) -> list[str]:
    """尺子**挡掉**的那些记号（证据，不是判定）：代码形状且词干含 `swap` / `replace`。"""
    out: list[str] = []
    for match in _CODE_TOKEN.finditer(text):
        token = match.group(0)
        if _is_code_identifier(token) and any(stem in token.casefold() for stem in BANNED_EN):
            out.append(token)
    return out


def src_wording_census(root: Path) -> tuple[int, int, int]:
    """扫 `root`（仓根的 `src`）：(文本文件数, `换成|替换` 处数, 命中行数)。纯读。"""
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix in SRC_SUFFIXES]
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)
    lines = sum(1 for line in text.splitlines() if _SRC_BANNED.search(line))
    return len(files), len(_SRC_BANNED.findall(text)), lines


# --------------------------------------------------------------------------- #
# 三个面的取法（① 递归取 CLI help；② __doc__；③ 真构造 plan 再调 summary）
# --------------------------------------------------------------------------- #
def _children(parser: argparse.ArgumentParser) -> list[argparse.ArgumentParser]:
    """一个 parser 直接挂着的子 parser（argparse 把 choices 存在 _SubParsersAction 上）。"""
    out: list[argparse.ArgumentParser] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            out.extend(action.choices.values())
    return out


def iter_help_texts(parser: argparse.ArgumentParser) -> list[str]:
    """顶层 + 每个子命令的 `format_help()`（**递归** —— 子命令里还能挂子命令）。"""
    texts = [parser.format_help()]
    for child in _children(parser):
        texts.extend(iter_help_texts(child))
    return texts


def cli_help_text() -> str:
    """① CLI 全部子命令帮助文本（顶层 + 20 个子命令，实测 21 份 / 13,465 字符）。"""
    return "\n".join(iter_help_texts(cli_main._build_parser()))


def _subcommand_help(name: str) -> str:
    """某个子命令在**顶层帮助列表**里那一行的短帮助（ADR 点名的那一处就是它）。"""
    for action in cli_main._build_parser()._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for pseudo in action._choices_actions:
            if pseudo.dest == name:
                return str(pseudo.help)
    raise AssertionError(f"CLI 里没有子命令 {name!r} —— 尺子的落点变了")


def criterion_text(tmp_path: Path) -> str:
    """⑤ `criterion_lines()` 的**真实输出**（CLI 的 skin plan 就打印这几行）。"""
    return "\n".join(_fake_plan(tmp_path).criterion_lines())


def patch_label_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """⑥ `install_swap` **真正渲染出来**的 patch_label（进补丁柜、`assets status` 会显示）。

    ⚠ 取法：这条 f-string 只在 `install_swap` 内部渲染，而它会走真写回。所以照
    `test_skin_install` 的老办法，把 `skin_install.writer.write_objects` 换成只记录 kwargs
    的替身 —— 拿到的是**真 f-string 的渲染结果**（不是抄一份字符串），且一个字节都不写盘。
    """
    plan = _fake_plan(tmp_path)
    seen: dict[str, object] = {}

    def fake_write_objects(bundle, changes, **kwargs):
        seen.update(kwargs)
        return "REPORT"

    monkeypatch.setattr(skin_install.writer, "write_objects", fake_write_objects)
    skin_install.install_swap(plan, project_root=tmp_path / "proj")
    return str(seen["patch_label"])


def scanned_texts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """六处操作文案面一次取全（键 = 文件头那张表的编号）。"""
    return {
        "①CLI": cli_help_text(),
        "②模块头": skin_install.__doc__ or "",
        "③summary": _fake_plan(tmp_path).summary(),
        "④类文档串": skin_install.SkinSwapPlan.__doc__ or "",
        "⑤criterion": criterion_text(tmp_path),
        "⑥patch_label": patch_label_text(tmp_path, monkeypatch),
    }


# --------------------------------------------------------------------------- #
# ① 尺子自己的正控 / 负控 / 边界（不能红/不能绿的尺子等于没有 · AGENTS §0.9 动作 2）
# --------------------------------------------------------------------------- #
def test_ruler_is_green_on_clean_copy() -> None:
    """正控：干净文案 0 命中 —— 尺子不许是恒红的。"""
    clean = (
        "修改材质槽 Layer_A97CDC25 的指向"
        "（旧值 BMD-2_1_BaseMap → 新值 BMD-2_3_BaseMap，8 字节）"
    )
    assert banned_hits(clean) == []
    assert banned_hits("修改 units 材质槽的指向（真机验过的那个 8 字节）") == []


@pytest.mark.parametrize(
    "sentence",
    [
        "把材质贴图换上另一张",
        "把材质槽换成别的贴图",
        "字符串替换",
        "更换材质槽的贴图",
        "swap the slot texture",
        "Replace the slot texture",
        "swapped it already",
        "把贴图SWAP掉",
        "把贴图swap掉",
    ],
)
def test_ruler_reports_every_banned_word(sentence: str) -> None:
    """负控：六个禁词**每一类**都要报出来（含中文贴着英文那一种）。"""
    assert banned_hits(sentence), f"尺子漏了：{sentence!r}"


def test_ruler_ignores_code_identifiers_but_shows_them() -> None:
    """边界：标识符名不算违规（Lead 定的范围），但它们必须**看得见**（不是暗着挡）。"""
    text = "SkinSwapPlan / plan_swap / install_swap / os.replace / --swap-yz"
    assert banned_hits(text) == []
    assert set(code_tokens_with_banned_stem(text)) == {
        "SkinSwapPlan",
        "plan_swap",
        "install_swap",
        "os.replace",
        "--swap-yz",
    }


# --------------------------------------------------------------------------- #
# ② 六处操作文案面：0 命中
# --------------------------------------------------------------------------- #
def test_cli_help_surface_is_not_empty() -> None:
    """取法正控：真取到了 CLI 文案 —— 否则"0 命中"是**扫空了**的假绿。"""
    text = cli_help_text()
    for name in ("gui", "info", "skin", "assets", "newasset"):
        assert name in text, f"CLI 帮助里没有 {name!r} —— 递归取法坏了？"
    assert len(text) > 5000, f"只取到 {len(text)} 字符 —— 子命令没取全"


def test_cli_help_text_carries_no_banned_wording() -> None:
    """① CLI 全部子命令帮助文本：0 命中。"""
    hits = banned_hits(cli_help_text())
    assert hits == [], f"CLI 帮助文本里有禁词：{hits}"


def test_cli_ruler_goes_red_when_a_violation_is_seeded_into_the_real_help() -> None:
    """J3 负控（照设计稿 §6 逐字）：往 `skin` 的 help 里塞一句「换上」，尺子**必须报出**。

    走的是**真渲染路径**（改 subparsers 的短帮助伪 action，再 `format_help()`），
    不是拿一段复制出来的文本 —— 这样才证明尺子读的是产品那一份。
    """
    parser = cli_main._build_parser()
    seeded = False
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for pseudo in action._choices_actions:
            if pseudo.dest == "skin":
                pseudo.help = "材质槽通道：把 units 材质贴图换上另一张"
                seeded = True
    assert seeded, "没找到 skin 子命令 —— 负控没塞进去，这一条等于没跑"

    hits = banned_hits("\n".join(iter_help_texts(parser)))
    assert "换上" in hits, f"塞了「换上」还全绿 —— 这把尺子是假的（hits={hits}）"


def test_skin_install_module_head_carries_no_banned_wording() -> None:
    """② 模块头：0 命中（真读 `skin_install.__doc__`）。"""
    doc = skin_install.__doc__ or ""
    assert len(doc) > 500, "模块头取不到 / 太短 —— 别拿空串当绿"
    hits = banned_hits(doc)
    assert hits == [], f"skin_install 模块头里有禁词：{hits}"


def test_skin_plan_summary_carries_no_banned_wording(tmp_path: Path) -> None:
    """③ `SkinSwapPlan.summary()` 的**真实返回文本**：0 命中（真构造再调）。"""
    summary = _fake_plan(tmp_path).summary()
    hits = banned_hits(summary)
    assert hits == [], f"summary() 里有禁词：{hits}\n{summary}"


def test_skin_plan_class_docstring_carries_no_banned_wording() -> None:
    """④ `SkinSwapPlan` 类文档串：0 命中（task-2 ①）。"""
    doc = skin_install.SkinSwapPlan.__doc__ or ""
    assert "修改材质槽的指向" in doc, doc
    hits = banned_hits(doc)
    assert hits == [], f"SkinSwapPlan 类文档串里有禁词：{hits}"


def test_criterion_lines_carries_no_banned_wording(tmp_path: Path) -> None:
    """⑤ `criterion_lines()` 的真实输出（CLI 会打印它）：0 命中（task-2 ②）。"""
    text = criterion_text(tmp_path)
    assert len(text) > 200, f"判据行太短 —— 取法坏了？\n{text}"
    hits = banned_hits(text)
    assert hits == [], f"criterion_lines() 里有禁词：{hits}\n{text}"


def test_patch_label_carries_no_banned_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⑥ `install_swap` 真正渲染出来的 patch_label（进补丁柜）：0 命中（task-2 ③）。"""
    text = patch_label_text(tmp_path, monkeypatch)
    assert text.startswith("皮肤："), text
    hits = banned_hits(text)
    assert hits == [], f"patch_label 里有禁词：{hits}\n{text}"


def test_new_surfaces_go_red_when_a_violation_is_seeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """扩面的负控：往 ④⑤⑥ 的**真文本**里塞一句「替换」，尺子必须报出。

    ⛔ 只对新写的句子能红不算数 —— 这三段是产品自己渲染出来的那一份。
    （①②③ 的负控在别处：CLI 那条走真渲染路径、③ 由上面那几条正控守着。）
    """
    for name, text in scanned_texts(tmp_path, monkeypatch).items():
        if name in ("①CLI", "②模块头", "③summary"):
            continue
        assert banned_hits(text) == [], f"{name} 本来就不干净：{banned_hits(text)}"
        seeded = text.replace("修改材质槽", "把原有贴图替换掉再", 1)
        assert seeded != text, f"{name} 里没找到锚点「修改材质槽」"
        assert "替换" in banned_hits(seeded), f"{name} 塞了「替换」还全绿：{seeded[:80]}"


def test_named_spots_state_modify_the_direction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点名的 6 处 sink：动词落「修改……的指向」，一个「换」字都不留（旧值/新值保留）。"""
    head = (skin_install.__doc__ or "").splitlines()[0]
    assert "修改材质槽的指向" in head, head
    assert "换" not in head, head

    summary = _fake_plan(tmp_path).summary()
    assert f"修改材质槽 {SLOT_A} 的指向" in summary, summary
    assert "换" not in summary, summary
    # 旧值 / 新值两个值保留（它们本身不是「换」）—— 别顺手删掉信息
    assert "BMD-2_1_BaseMap" in summary and "BMD-2_3_BaseMap" in summary, summary

    skin_help = _subcommand_help("skin")
    assert "修改 units 材质槽的指向" in skin_help, skin_help
    assert "换" not in skin_help, skin_help

    cls_doc = skin_install.SkinSwapPlan.__doc__ or ""
    assert "修改材质槽的指向" in cls_doc and "换图" not in cls_doc, cls_doc

    criterion = criterion_text(tmp_path)
    assert "[修改材质槽的指向]" in criterion and "换" not in criterion, criterion
    assert "旧值 BMD-2_1_BaseMap" in criterion, criterion
    assert "新值 BMD-2_3_BaseMap" in criterion, criterion

    label = patch_label_text(tmp_path, monkeypatch)
    assert f"修改材质槽 {SLOT_A} 的指向" in label, label
    assert "旧值 BMD-2_1_BaseMap" in label and "新值 BMD-2_3_BaseMap" in label, label
    assert "换" not in label, label


def test_only_known_identifiers_carry_the_swap_stem_on_the_six_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """尺子**挡掉**了哪些东西，要看得见：六处文本上只能是这三个已知标识符。

    ⛔ 这不是"豁免名单"的入口：集合一变就是**新的 swap / replace 命名**进了文案面，
    得有人过一眼（ADR 0008：⛔ 不许有 `object swap` / `--replace` 这类"以 B 顶替 A"的入口）。
    """
    seen: set[str] = set()
    for text in scanned_texts(tmp_path, monkeypatch).values():
        seen |= set(code_tokens_with_banned_stem(text))
    expected = {"--swap-yz", "plan_swap", "install_swap"}
    assert seen == expected, f"文案面的代码记号变了：{sorted(seen)}"


# --------------------------------------------------------------------------- #
# ③ 分母读数（那条"一条命令复算"的量法）
# --------------------------------------------------------------------------- #
def test_src_census_is_not_vacuous() -> None:
    """全仓 `src` 的分母：`-k src_census -s -n 0` 能一条命令复算（文件头的数字就是它）。

    只判死两件：① 扫到的文件不是个位数；② 命中数**不是 0**。
    具体数字不写进断言 —— 它会随别处（导航文案 / 文件替换）改动漂，写死就是给自己埋雷。
    """
    root = Path(__file__).resolve().parents[1] / "src"
    files, occurrences, lines = src_wording_census(root)
    print(f"\n[分母] {files} 个文本文件 | 换成|替换 = {occurrences} 处 / {lines} 行")
    assert files > 50, f"只扫到 {files} 个文件 —— 分母指错了？"
    assert occurrences > 0, "全仓 0 命中 ⇒ 大概率是扫空了（2026-10-08 实测 284 处）"
